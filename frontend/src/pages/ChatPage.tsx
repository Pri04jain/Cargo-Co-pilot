/**
 * Ask Copilot chat page.
 *
 * UI:
 *   - Message thread at top, input at bottom (standard chat layout)
 *   - Each assistant message shows inline citations that link to the source doc
 *   - Insufficient-context answers shown with a different style (no citations)
 *   - Example questions shown when the thread is empty
 *
 * Citations:
 *   Each [DOC:id:page] reference in the answer is rendered as a clickable chip.
 *   Clicking navigates to /documents/{id} so the reviewer can see the source.
 */

import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";
import { Send, Bot, User, FileText, AlertCircle } from "lucide-react";
import { api } from "../lib/api";

interface Citation {
  document_id: string;
  page_number: number;
  filename: string;
  snippet: string;
}

interface Message {
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
  sufficient_context?: boolean;
  tokens_used?: number;
}

const EXAMPLE_QUESTIONS = [
  "Which shipments have a weight mismatch?",
  "What is the incoterm on the most recent invoice?",
  "Are there any invoices that don't match their purchase order?",
  "What containers are on the latest bill of lading?",
  "Show me all documents with validation errors.",
];

export default function ChatPage() {
  const navigate = useNavigate();
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  const chatMut = useMutation({
    mutationFn: (question: string) =>
      api.post("/api/v1/chat", { question }).then((r) => r.data),
    onSuccess: (data) => {
      const msg: Message = {
        role: "assistant",
        content: data.answer,
        citations: data.citations,
        sufficient_context: data.sufficient_context,
        tokens_used: data.tokens_used,
      };
      setMessages((prev) => [...prev, msg]);
      setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }), 50);
    },
    onError: () => {
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: "Sorry, something went wrong. Please try again.",
          sufficient_context: false,
        },
      ]);
    },
  });

  const submit = () => {
    const q = input.trim();
    if (!q || chatMut.isPending) return;
    setMessages((prev) => [...prev, { role: "user", content: q }]);
    setInput("");
    chatMut.mutate(q);
    setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }), 50);
  };

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="border-b border-gray-200 px-6 py-4 bg-white">
        <div className="flex items-center gap-3">
          <div className="bg-blue-100 p-2 rounded-lg">
            <Bot className="w-5 h-5 text-blue-600" />
          </div>
          <div>
            <h2 className="font-semibold text-gray-900">Ask Copilot</h2>
            <p className="text-xs text-gray-500">
              Ask questions about your processed shipping documents
            </p>
          </div>
        </div>
      </div>

      {/* Message thread */}
      <div className="flex-1 overflow-auto px-6 py-4 space-y-4">
        {messages.length === 0 ? (
          <div className="max-w-2xl mx-auto mt-8">
            <div className="text-center mb-8">
              <Bot className="w-12 h-12 text-blue-200 mx-auto mb-3" />
              <p className="text-gray-500 text-sm">
                Ask anything about your shipping documents.
                <br />
                I'll cite the exact source for every answer.
              </p>
            </div>
            <p className="text-xs font-medium text-gray-400 uppercase tracking-wide mb-3">
              Try asking…
            </p>
            <div className="space-y-2">
              {EXAMPLE_QUESTIONS.map((q) => (
                <button
                  key={q}
                  onClick={() => {
                    setInput(q);
                  }}
                  className="w-full text-left px-4 py-3 bg-white border border-gray-200 rounded-lg text-sm text-gray-600 hover:border-blue-300 hover:bg-blue-50 transition-colors"
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="max-w-3xl mx-auto space-y-4">
            {messages.map((msg, i) => (
              <MessageBubble
                key={i}
                message={msg}
                onCitationClick={(docId) => navigate(`/documents/${docId}`)}
              />
            ))}
            {chatMut.isPending && (
              <div className="flex gap-3 items-start">
                <div className="bg-blue-100 p-2 rounded-lg shrink-0">
                  <Bot className="w-4 h-4 text-blue-600" />
                </div>
                <div className="bg-white border border-gray-200 rounded-xl px-4 py-3">
                  <div className="flex gap-1">
                    {[0, 1, 2].map((i) => (
                      <div
                        key={i}
                        className="w-2 h-2 bg-gray-400 rounded-full animate-bounce"
                        style={{ animationDelay: `${i * 0.15}s` }}
                      />
                    ))}
                  </div>
                </div>
              </div>
            )}
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input bar */}
      <div className="border-t border-gray-200 px-6 py-4 bg-white">
        <div className="max-w-3xl mx-auto flex gap-3">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && submit()}
            placeholder="Ask about your documents…"
            className="flex-1 border border-gray-300 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            disabled={chatMut.isPending}
          />
          <button
            onClick={submit}
            disabled={!input.trim() || chatMut.isPending}
            className="bg-blue-600 text-white px-4 py-2.5 rounded-xl hover:bg-blue-700 disabled:opacity-50 transition-colors"
          >
            <Send className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  );
}

function MessageBubble({
  message,
  onCitationClick,
}: {
  message: Message;
  onCitationClick: (docId: string) => void;
}) {
  if (message.role === "user") {
    return (
      <div className="flex gap-3 items-start justify-end">
        <div className="bg-blue-600 text-white rounded-xl px-4 py-3 max-w-lg text-sm">
          {message.content}
        </div>
        <div className="bg-gray-200 p-2 rounded-lg shrink-0">
          <User className="w-4 h-4 text-gray-600" />
        </div>
      </div>
    );
  }

  const insufficient = message.sufficient_context === false;

  return (
    <div className="flex gap-3 items-start">
      <div className={`p-2 rounded-lg shrink-0 ${insufficient ? "bg-yellow-100" : "bg-blue-100"}`}>
        {insufficient ? (
          <AlertCircle className="w-4 h-4 text-yellow-600" />
        ) : (
          <Bot className="w-4 h-4 text-blue-600" />
        )}
      </div>

      <div className="flex-1 min-w-0">
        <div
          className={`rounded-xl px-4 py-3 text-sm leading-relaxed ${
            insufficient
              ? "bg-yellow-50 border border-yellow-200 text-yellow-800"
              : "bg-white border border-gray-200 text-gray-800"
          }`}
        >
          {/* Render answer with inline citation chips */}
          <AnswerWithCitations
            answer={message.content}
            citations={message.citations ?? []}
            onCitationClick={onCitationClick}
          />
        </div>

        {/* Citation cards */}
        {message.citations && message.citations.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-2">
            {message.citations.map((c, i) => (
              <button
                key={i}
                onClick={() => onCitationClick(c.document_id)}
                className="flex items-center gap-1.5 text-xs bg-white border border-gray-200 rounded-lg px-2.5 py-1.5 text-gray-600 hover:border-blue-300 hover:bg-blue-50 transition-colors"
              >
                <FileText className="w-3 h-3 shrink-0" />
                <span className="truncate max-w-32">{c.filename}</span>
                <span className="text-gray-400">p.{c.page_number}</span>
              </button>
            ))}
          </div>
        )}

        {/* Cost indicator */}
        {message.tokens_used != null && message.tokens_used > 0 && (
          <p className="text-xs text-gray-300 mt-1.5">
            {message.tokens_used} tokens
          </p>
        )}
      </div>
    </div>
  );
}

function AnswerWithCitations({
  answer,
  citations,
  onCitationClick,
}: {
  answer: string;
  citations: Citation[];
  onCitationClick: (docId: string) => void;
}) {
  // Replace [DOC:uuid:page] with a clickable chip
  const parts = answer.split(/(\[DOC:[^\]]+\])/g);
  return (
    <>
      {parts.map((part, i) => {
        const match = part.match(/^\[DOC:([^:]+):(\d+)\]$/);
        if (match) {
          const docId = match[1];
          const page = match[2];
          const citation = citations.find((c) => c.document_id === docId);
          return (
            <button
              key={i}
              onClick={() => onCitationClick(docId)}
              className="inline-flex items-center gap-1 bg-blue-100 text-blue-700 text-xs px-1.5 py-0.5 rounded font-medium hover:bg-blue-200 transition-colors mx-0.5"
            >
              <FileText className="w-2.5 h-2.5" />
              {citation?.filename?.slice(0, 20) ?? docId.slice(0, 8)}:p{page}
            </button>
          );
        }
        return <span key={i}>{part}</span>;
      })}
    </>
  );
}
