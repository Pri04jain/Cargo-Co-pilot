/**
 * PDF Viewer with evidence highlight overlay.
 *
 * How highlights work:
 *   1. We render the PDF using react-pdf's <Page> component.
 *   2. On top of the rendered canvas we place an absolutely-positioned SVG
 *      that covers the same dimensions.
 *   3. When a field is selected, we draw a semi-transparent rectangle at the
 *      bbox coordinates, scaled to match the rendered page size.
 *
 * Coordinate system:
 *   - PDF coordinates: points, origin top-left, from PyMuPDF.
 *   - Page dimensions (width/height) come from the parsed document_pages record.
 *   - We scale bbox coordinates:
 *       rendered_x = bbox.x0 / page_width_pts * rendered_width_px
 *
 * Why SVG overlay instead of canvas?
 *   - SVG rectangles are easier to animate (CSS transitions).
 *   - They don't interfere with react-pdf's own canvas rendering.
 *   - They can be keyboard-accessible (role="img", aria-label).
 */

import { useCallback, useRef, useState, useEffect } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";
import type { BBox } from "../types";
import workerSrc from "pdfjs-dist/build/pdf.worker.min.js?url";

// Tell react-pdf where the PDF.js worker lives.
// ?url tells Vite to serve this file as a static asset and give us its URL.
pdfjs.GlobalWorkerOptions.workerSrc = workerSrc;

interface Highlight {
  page: number;
  bbox: BBox;
  color?: string;
  label?: string;
}

interface Props {
  fileUrl: string;           // URL to fetch the PDF from
  pageWidth?: number;        // rendered width in px (default 600)
  highlight: Highlight | null;
  pageWidthPts?: number;     // page width in PDF points (from parsed document)
  pageHeightPts?: number;
  onPageCountChange?: (n: number) => void;
}

export default function PdfViewer({
  fileUrl,
  pageWidth = 600,
  highlight,
  pageWidthPts,
  pageHeightPts,
  onPageCountChange,
}: Props) {
  const [numPages, setNumPages] = useState(0);
  const [currentPage, setCurrentPage] = useState(1);
  const [renderedSize, setRenderedSize] = useState<{ w: number; h: number } | null>(null);
  const [pdfBlob, setPdfBlob] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // Fetch PDF with authentication header
  useEffect(() => {
    let objectUrl: string | null = null;

    const fetchPdf = async () => {
      try {
        setLoadError(null);
        // Token is stored under "cargo-copilot-auth" by Zustand persist
        const authRaw = localStorage.getItem("cargo-copilot-auth");
        const token = authRaw ? JSON.parse(authRaw)?.state?.token : null;

        const response = await fetch(fileUrl, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }

        const blob = await response.blob();
        objectUrl = URL.createObjectURL(blob);
        setPdfBlob(objectUrl);
      } catch (error: any) {
        console.error("Failed to fetch PDF:", error);
        setLoadError(error.message || "Failed to load PDF");
        setPdfBlob(null);
      }
    };

    fetchPdf();

    return () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [fileUrl]);

  const onDocumentLoad = useCallback(
    ({ numPages }: { numPages: number }) => {
      setNumPages(numPages);
      onPageCountChange?.(numPages);
    },
    [onPageCountChange]
  );

  const onPageRender = useCallback(() => {
    // Use getBoundingClientRect for accurate rendered pixel dimensions
    if (containerRef.current) {
      const canvas = containerRef.current.querySelector("canvas");
      if (canvas) {
        const rect = canvas.getBoundingClientRect();
        setRenderedSize({ w: rect.width, h: rect.height });
      }
    }
  }, []);

  // Re-measure canvas when page dimensions arrive (fixes timing issue)
  useEffect(() => {
    if (pageWidthPts && containerRef.current) {
      const canvas = containerRef.current.querySelector("canvas");
      if (canvas) {
        const rect = canvas.getBoundingClientRect();
        if (rect.width > 0) setRenderedSize({ w: rect.width, h: rect.height });
      }
    }
  }, [pageWidthPts, pageHeightPts]);

  // Scale a PDF-point bbox to CSS pixels
  const scaleBbox = (bbox: BBox, rw: number, rh: number, ptW: number, ptH: number) => ({
    left:   (bbox.x0 / ptW) * rw,
    top:    (bbox.y0 / ptH) * rh,
    width:  ((bbox.x1 - bbox.x0) / ptW) * rw,
    height: ((bbox.y1 - bbox.y0) / ptH) * rh,
  });

  const showHighlight =
    highlight &&
    highlight.page === currentPage &&
    highlight.bbox &&
    renderedSize &&
    pageWidthPts &&
    pageHeightPts;

  const scaled = showHighlight
    ? scaleBbox(highlight!.bbox, renderedSize!.w, renderedSize!.h, pageWidthPts!, pageHeightPts!)
    : null;

  // Show loading state while fetching PDF
  if (loadError) {
    return (
      <div className="w-full h-96 flex flex-col items-center justify-center">
        <div className="text-red-500 text-sm mb-2">Failed to load PDF</div>
        <div className="text-gray-400 text-xs">{loadError}</div>
      </div>
    );
  }

  if (!pdfBlob) {
    return (
      <div className="w-full h-96 flex items-center justify-center text-gray-400">
        Loading PDF…
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {/* Page navigation */}
      {numPages > 1 && (
        <div className="flex items-center justify-between text-sm text-gray-500 px-2">
          <button
            onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
            disabled={currentPage === 1}
            className="px-3 py-1 border rounded disabled:opacity-40 hover:bg-gray-50"
          >
            ←
          </button>
          <span>Page {currentPage} of {numPages}</span>
          <button
            onClick={() => setCurrentPage((p) => Math.min(numPages, p + 1))}
            disabled={currentPage === numPages}
            className="px-3 py-1 border rounded disabled:opacity-40 hover:bg-gray-50"
          >
            →
          </button>
        </div>
      )}

      {/* PDF + overlay */}
      <div ref={containerRef} className="relative inline-block border border-gray-200 rounded overflow-hidden shadow-sm">
        <Document
          file={pdfBlob}
          onLoadSuccess={onDocumentLoad}
          loading={<div className="w-full h-96 flex items-center justify-center text-gray-400">Rendering PDF…</div>}
          error={<div className="w-full h-32 flex items-center justify-center text-red-500 text-sm">
            Failed to render PDF. Check browser console for details.
          </div>}
        >
          <Page
            pageNumber={currentPage}
            width={pageWidth}
            onRenderSuccess={onPageRender}
            renderTextLayer={false}
            renderAnnotationLayer={false}
          />
        </Document>

        {/* Highlight overlay */}
        {scaled && (
          <svg
            className="absolute inset-0 pointer-events-none"
            style={{ width: renderedSize!.w, height: renderedSize!.h }}
            aria-label={`Evidence highlight for ${highlight!.label || "field"}`}
            role="img"
          >
            <rect
              x={scaled.left}
              y={scaled.top}
              width={scaled.width}
              height={scaled.height}
              fill={highlight!.color || "rgba(59, 130, 246, 0.25)"}
              stroke={highlight!.color ? highlight!.color.replace("0.25", "0.8") : "rgba(59, 130, 246, 0.8)"}
              strokeWidth={2}
              rx={2}
              className="transition-all duration-200"
            />
          </svg>
        )}
      </div>
    </div>
  );
}
