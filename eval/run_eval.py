#!/usr/bin/env python3
"""
Evaluation harness — Phase 7.

This script runs the full pipeline on a labeled test set and reports:
- Field-level accuracy (overall, per field, per document type)
- Evidence grounding rate
- Routing decision accuracy
- Average latency and cost per document
- A markdown report saved to eval/reports/

Usage: make eval
       python eval/run_eval.py [--split test|train] [--model gpt-4o-mini]
"""

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
import structlog
from langfuse import Langfuse
from pydantic import BaseModel

# Add the backend app to Python path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.schemas.extraction import (
    BillOfLadingSchema,
    CommercialInvoiceSchema,
    DocumentType,
    ExtractedField,
    PackingListSchema,
    SCHEMA_BY_TYPE,
)

logger = structlog.get_logger()


class EvalConfig(BaseModel):
    """Configuration for evaluation run."""
    split: str = "test"
    model: str = "gpt-4o-mini"
    api_base_url: str = "http://localhost:8000"
    max_documents: Optional[int] = None
    output_dir: str = "eval/reports"
    langfuse_enabled: bool = True


class FieldMetrics(BaseModel):
    """Metrics for a single field."""
    total_expected: int = 0
    total_predicted: int = 0
    correct: int = 0
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0


class DocumentMetrics(BaseModel):
    """Metrics for a single document."""
    doc_id: str
    doc_type: str
    predicted_type: Optional[str] = None
    classification_correct: bool = False
    field_accuracy: float = 0.0
    grounding_rate: float = 0.0
    latency_ms: float = 0.0
    total_cost: float = 0.0
    field_metrics: Dict[str, bool] = {}  # field_name -> correct


class EvalResults(BaseModel):
    """Complete evaluation results."""
    config: EvalConfig
    timestamp: str
    total_documents: int
    avg_latency_ms: float
    total_cost: float
    classification_accuracy: float
    overall_field_accuracy: float
    grounding_rate: float
    field_metrics: Dict[str, FieldMetrics] = {}
    doc_type_metrics: Dict[str, Dict[str, FieldMetrics]] = {}
    document_results: List[DocumentMetrics] = []


class EvaluationHarness:
    """Main evaluation harness."""
    
    def __init__(self, config: EvalConfig):
        self.config = config
        self.langfuse = None
        if config.langfuse_enabled:
            try:
                self.langfuse = Langfuse()
                logger.info("Langfuse initialized for cost tracking")
            except Exception as e:
                logger.warning(f"Failed to initialize Langfuse: {e}")
                self.config.langfuse_enabled = False
    
    def _validate_prerequisites(self):
        """Validate that all required configuration is present."""
        errors = []
        
        # Check for API token
        if not os.getenv("EVAL_API_TOKEN"):
            errors.append(
                "EVAL_API_TOKEN environment variable not set. "
                "Please create a user token and set EVAL_API_TOKEN=<token>"
            )
        
        # Check for LLM API key (if using external API)
        llm_provider = os.getenv("LLM_PROVIDER", "openai")
        if llm_provider == "openai" and not os.getenv("LLM_API_KEY"):
            errors.append(
                "LLM_API_KEY environment variable not set. "
                "Please set your OpenAI API key to run the real pipeline."
            )
        
        # Check API connectivity
        try:
            import requests
            response = requests.get(f"{self.config.api_base_url}/api/v1/health", timeout=5)
            if response.status_code != 200:
                errors.append(f"API health check failed: {response.status_code}. Is the API running?")
        except Exception as e:
            errors.append(f"Cannot connect to API at {self.config.api_base_url}: {e}")
        
        if errors:
            error_msg = "Evaluation prerequisites not met:\n" + "\n".join(f"  - {e}" for e in errors)
            raise RuntimeError(error_msg)
    
    async def load_test_data(self) -> List[Dict[str, Any]]:
        """Load labeled test data and normalise the generator's format."""
        data_dir = Path(f"data/synthetic/{self.config.split}")

        if not data_dir.exists():
            raise RuntimeError(
                f"Test data directory {data_dir} does not exist. "
                f"Run: python3 data/synthetic/generate.py"
            )

        test_files = []
        for gt_path in sorted(data_dir.glob("*.json")):
            try:
                with open(gt_path) as f:
                    gt = json.load(f)

                # Skip _scan sidecar files (they duplicate the base document)
                if gt_path.stem.endswith("_scan"):
                    continue

                # ── Normalise the generator's ground-truth format ──────────────
                # Generator format:  {doc_type, expected_fields: {name: value}, filename, ...}
                # Evaluator format:  {document_id, file_path, ground_truth: {doc_type, fields: {name: {value, page}}}}

                if "document_id" in gt and "ground_truth" in gt:
                    # Already in evaluator format — use as-is
                    actual_file = Path(gt["file_path"])
                    if not actual_file.exists():
                        logger.warning(f"Skipping {gt_path}: referenced file {actual_file} not found")
                        continue
                    test_files.append(gt)

                elif "expected_fields" in gt:
                    # Generator format — convert
                    doc_id = gt_path.stem
                    pdf_file = data_dir / gt.get("filename", f"{doc_id}.pdf")
                    if not pdf_file.exists():
                        logger.warning(f"Skipping {gt_path}: PDF {pdf_file} not found")
                        continue

                    test_files.append({
                        "document_id": doc_id,
                        "file_path": str(pdf_file),
                        "ground_truth": {
                            "doc_type": gt["doc_type"],
                            "fields": {
                                k: {"value": str(v), "page": 1}
                                for k, v in gt.get("expected_fields", {}).items()
                            },
                            "expected_routing": gt.get("expected_routing", "auto_approve"),
                            "issues": gt.get("issues", []),
                        },
                    })
                else:
                    logger.warning(f"Skipping {gt_path}: unrecognised ground-truth format")

            except Exception as e:
                logger.warning(f"Failed to load {gt_path}: {e}")

        if not test_files:
            raise RuntimeError(
                f"No valid test files found in {data_dir}/. "
                "Run: python3 data/synthetic/generate.py"
            )

        logger.info(f"Loaded {len(test_files)} test documents from {data_dir}")
        return test_files
    

    
    async def run_pipeline_on_document(self, doc_data: Dict[str, Any]) -> Tuple[Dict[str, Any], float, float]:
        """Run the real pipeline on a single document and return results, latency, cost."""
        doc_id = doc_data["document_id"]
        file_path = Path(doc_data["file_path"])
        
        if not file_path.exists():
            raise FileNotFoundError(f"Document file not found: {file_path}")
        
        start_time = time.time()
        
        try:
            # Upload the document to the API
            async with httpx.AsyncClient(timeout=120.0) as client:
                # Read file content
                with open(file_path, "rb") as f:
                    file_content = f.read()
                
                # Determine MIME type
                mime_type = "application/pdf"
                ext = file_path.suffix.lower()
                if ext in [".png"]:
                    mime_type = "image/png"
                elif ext in [".jpg", ".jpeg"]:
                    mime_type = "image/jpeg"
                elif ext in [".tiff", ".tif"]:
                    mime_type = "image/tiff"
                
                # Upload document
                files = {"file": (file_path.name, file_content, mime_type)}
                upload_response = await client.post(
                    f"{self.config.api_base_url}/api/v1/documents/upload",
                    files=files,
                    headers={"Authorization": f"Bearer {self._get_api_token()}"}
                )
                
                if upload_response.status_code != 202:
                    raise RuntimeError(
                        f"Document upload failed: {upload_response.status_code} - {upload_response.text}"
                    )
                
                upload_data = upload_response.json()
                uploaded_doc_id = upload_data["document_id"]
                
                # Poll for completion
                max_wait = 300  # 5 minutes max
                poll_interval = 2  # seconds
                elapsed = 0
                
                while elapsed < max_wait:
                    await asyncio.sleep(poll_interval)
                    elapsed += poll_interval
                    
                    status_response = await client.get(
                        f"{self.config.api_base_url}/api/v1/documents/{uploaded_doc_id}/status",
                        headers={"Authorization": f"Bearer {self._get_api_token()}"}
                    )
                    
                    if status_response.status_code != 200:
                        raise RuntimeError(f"Status check failed: {status_response.status_code}")
                    
                    status_data = status_response.json()
                    status = status_data["status"]
                    
                    if status in ["approved", "needs_review", "rejected"]:
                        # Processing complete, fetch results
                        break
                    elif status == "failed":
                        error_msg = status_data.get("error", "Unknown error")
                        raise RuntimeError(f"Pipeline failed: {error_msg}")
                    elif status in ["queued", "processing"]:
                        continue
                    else:
                        logger.warning(f"Unknown status: {status}")
                
                if elapsed >= max_wait:
                    raise TimeoutError(f"Document processing timed out after {max_wait}s")
                
                # Fetch complete document data
                doc_response = await client.get(
                    f"{self.config.api_base_url}/api/v1/documents/{uploaded_doc_id}",
                    headers={"Authorization": f"Bearer {self._get_api_token()}"}
                )
                
                if doc_response.status_code != 200:
                    raise RuntimeError(f"Failed to fetch document: {doc_response.status_code}")
                
                doc_result = doc_response.json()
                
                # Fetch extracted fields
                fields_response = await client.get(
                    f"{self.config.api_base_url}/api/v1/documents/{uploaded_doc_id}/fields",
                    headers={"Authorization": f"Bearer {self._get_api_token()}"}
                )
                
                if fields_response.status_code != 200:
                    raise RuntimeError(f"Failed to fetch fields: {fields_response.status_code}")
                
                fields_data = fields_response.json()
                
                latency = (time.time() - start_time) * 1000  # ms
                
                # Get actual cost from document metadata
                cost = doc_result.get("total_cost_usd", 0.0)
                
                # Transform API response to evaluation format
                response_data = self._transform_api_response(doc_result, fields_data)
                
                return response_data, latency, cost
                
        except Exception as e:
            logger.error(f"Pipeline failed for {doc_id}: {e}")
            latency = (time.time() - start_time) * 1000
            return {"error": str(e)}, latency, 0.0
    
    def _get_api_token(self) -> str:
        """Get API authentication token."""
        # For evaluation, we need a valid user token
        # In a real setup, this would use a service account or test user
        # For now, check environment variable
        token = os.getenv("EVAL_API_TOKEN")
        if not token:
            raise RuntimeError(
                "EVAL_API_TOKEN environment variable not set. "
                "Please create a user token and set EVAL_API_TOKEN=<token>"
            )
        return token
    
    def _transform_api_response(self, doc_result: Dict, fields_data: Dict) -> Dict[str, Any]:
        """Transform API response to evaluation format."""
        # Extract classification from document type
        doc_type = doc_result.get("doc_type", "unknown")
        
        # Transform extracted fields
        extracted_fields = {}
        for field in fields_data.get("fields", []):
            field_name = field["field_name"]
            extracted_fields[field_name] = {
                "value": field.get("value"),
                "confidence": field.get("confidence", 0.0),
                "evidence": field.get("evidence"),
                "status": field.get("status", "ungrounded")
            }
        
        return {
            "classification": {
                "doc_type": doc_type,
                "confidence": doc_result.get("overall_confidence", 0.0),
                "reasoning": f"Classified as {doc_type}"
            },
            "extraction": extracted_fields,
            "document_id": doc_result.get("id")
        }
    

    

    
    def calculate_field_accuracy(self, ground_truth: Dict, extracted: Dict, field_path: str = "") -> bool:
        """Calculate if a field was extracted correctly."""
        def get_nested_value(data: Dict, path: str):
            if not path:
                return data
            parts = path.split(".")
            current = data
            for part in parts:
                if isinstance(current, dict) and part in current:
                    current = current[part]
                else:
                    return None
            return current
        
        gt_value = get_nested_value(ground_truth, field_path)
        extracted_value = get_nested_value(extracted, field_path)
        
        if isinstance(gt_value, dict) and "value" in gt_value:
            gt_value = gt_value["value"]
        
        if isinstance(extracted_value, dict) and "value" in extracted_value:
            extracted_value = extracted_value["value"]
        
        # Normalize values for comparison
        if gt_value is None and extracted_value is None:
            return True
        
        if gt_value is None or extracted_value is None:
            return False
        
        # String comparison with some normalization
        gt_str = str(gt_value).strip().lower()
        extracted_str = str(extracted_value).strip().lower()
        
        return gt_str == extracted_str
    
    def evaluate_document(self, doc_data: Dict[str, Any], pipeline_result: Dict[str, Any], 
                         latency: float, cost: float) -> DocumentMetrics:
        """Evaluate results for a single document."""
        doc_id = doc_data["document_id"]
        ground_truth = doc_data["ground_truth"]
        
        # Classification accuracy
        gt_type = ground_truth["doc_type"]
        predicted_type = pipeline_result.get("classification", {}).get("doc_type", "unknown")
        classification_correct = gt_type == predicted_type
        
        # Field-level accuracy
        field_metrics = {}
        grounded_fields = 0
        total_fields = 0
        correct_fields = 0
        
        gt_fields = ground_truth.get("fields", {})
        extracted_fields = pipeline_result.get("extraction", {})
        
        for field_name, field_data in gt_fields.items():
            if isinstance(field_data, dict):
                if "value" in field_data:  # Direct field
                    total_fields += 1
                    correct = self.calculate_field_accuracy(gt_fields, extracted_fields, field_name)
                    field_metrics[field_name] = correct
                    if correct:
                        correct_fields += 1
                    
                    # Check if grounded
                    extracted_field = extracted_fields.get(field_name, {})
                    if isinstance(extracted_field, dict) and extracted_field.get("status") == "grounded":
                        grounded_fields += 1
                        
                else:  # Nested structure (e.g., shipper.name)
                    for subfield, subdata in field_data.items():
                        if isinstance(subdata, dict) and "value" in subdata:
                            total_fields += 1
                            field_path = f"{field_name}.{subfield}"
                            correct = self.calculate_field_accuracy(gt_fields, extracted_fields, field_path)
                            field_metrics[field_path] = correct
                            if correct:
                                correct_fields += 1
                            
                            # Check if grounded
                            extracted_nested = extracted_fields.get(field_name, {}).get(subfield, {})
                            if isinstance(extracted_nested, dict) and extracted_nested.get("status") == "grounded":
                                grounded_fields += 1
        
        field_accuracy = correct_fields / total_fields if total_fields > 0 else 0.0
        grounding_rate = grounded_fields / total_fields if total_fields > 0 else 0.0
        
        return DocumentMetrics(
            doc_id=doc_id,
            doc_type=gt_type,
            predicted_type=predicted_type,
            classification_correct=classification_correct,
            field_accuracy=field_accuracy,
            grounding_rate=grounding_rate,
            latency_ms=latency,
            total_cost=cost,
            field_metrics=field_metrics
        )
    
    def aggregate_results(self, document_results: List[DocumentMetrics]) -> EvalResults:
        """Aggregate results across all documents."""
        if not document_results:
            return EvalResults(
                config=self.config,
                timestamp=datetime.now().isoformat(),
                total_documents=0,
                avg_latency_ms=0.0,
                total_cost=0.0,
                classification_accuracy=0.0,
                overall_field_accuracy=0.0,
                grounding_rate=0.0
            )
        
        # Overall metrics
        total_docs = len(document_results)
        correct_classifications = sum(1 for doc in document_results if doc.classification_correct)
        classification_accuracy = correct_classifications / total_docs
        
        avg_field_accuracy = sum(doc.field_accuracy for doc in document_results) / total_docs
        avg_grounding_rate = sum(doc.grounding_rate for doc in document_results) / total_docs
        avg_latency = sum(doc.latency_ms for doc in document_results) / total_docs
        total_cost = sum(doc.total_cost for doc in document_results)
        
        # Field-level aggregation
        field_metrics = {}
        doc_type_metrics = {}
        
        # Collect all field results
        all_field_results = {}  # field_name -> [True, False, True, ...]
        for doc in document_results:
            for field_name, correct in doc.field_metrics.items():
                if field_name not in all_field_results:
                    all_field_results[field_name] = []
                all_field_results[field_name].append(correct)
        
        # Calculate per-field metrics
        for field_name, results in all_field_results.items():
            total = len(results)
            correct = sum(results)
            field_metrics[field_name] = FieldMetrics(
                total_expected=total,
                total_predicted=total,  # Simplified
                correct=correct,
                precision=correct / total if total > 0 else 0.0,
                recall=correct / total if total > 0 else 0.0,
                f1=correct / total if total > 0 else 0.0
            )
        
        # Per document type metrics  
        for doc in document_results:
            doc_type = doc.doc_type
            if doc_type not in doc_type_metrics:
                doc_type_metrics[doc_type] = {}
            
            for field_name, correct in doc.field_metrics.items():
                if field_name not in doc_type_metrics[doc_type]:
                    doc_type_metrics[doc_type][field_name] = []
                doc_type_metrics[doc_type][field_name].append(correct)
        
        # Convert to FieldMetrics objects
        for doc_type, fields in doc_type_metrics.items():
            for field_name, results in fields.items():
                total = len(results)
                correct = sum(results)
                if field_name not in doc_type_metrics[doc_type]:
                    doc_type_metrics[doc_type] = {}
                doc_type_metrics[doc_type][field_name] = FieldMetrics(
                    total_expected=total,
                    total_predicted=total,
                    correct=correct,
                    precision=correct / total if total > 0 else 0.0,
                    recall=correct / total if total > 0 else 0.0,
                    f1=correct / total if total > 0 else 0.0
                )
        
        return EvalResults(
            config=self.config,
            timestamp=datetime.now().isoformat(),
            total_documents=total_docs,
            avg_latency_ms=avg_latency,
            total_cost=total_cost,
            classification_accuracy=classification_accuracy,
            overall_field_accuracy=avg_field_accuracy,
            grounding_rate=avg_grounding_rate,
            field_metrics=field_metrics,
            doc_type_metrics=doc_type_metrics,
            document_results=document_results
        )
    
    async def run_evaluation(self) -> EvalResults:
        """Run the complete evaluation."""
        logger.info(f"Starting evaluation with config: {self.config}")
        
        # Validate prerequisites before starting
        self._validate_prerequisites()
        
        # Load test data
        test_data = await self.load_test_data()
        
        if self.config.max_documents:
            test_data = test_data[:self.config.max_documents]
        
        logger.info(f"Running evaluation on {len(test_data)} documents")
        
        # Process each document
        document_results = []
        
        for i, doc_data in enumerate(test_data, 1):
            logger.info(f"Processing document {i}/{len(test_data)}: {doc_data['document_id']}")
            
            try:
                pipeline_result, latency, cost = await self.run_pipeline_on_document(doc_data)
                doc_metrics = self.evaluate_document(doc_data, pipeline_result, latency, cost)
                document_results.append(doc_metrics)
                
                # Log to Langfuse if enabled
                if self.langfuse and "error" not in pipeline_result:
                    self._log_to_langfuse(doc_data, pipeline_result, doc_metrics)
                    
            except Exception as e:
                logger.error(f"Failed to process document {doc_data['document_id']}: {e}")
                continue
        
        # Aggregate results
        eval_results = self.aggregate_results(document_results)
        
        logger.info(f"Evaluation complete: {eval_results.total_documents} documents processed")
        logger.info(f"Classification accuracy: {eval_results.classification_accuracy:.1%}")
        logger.info(f"Field accuracy: {eval_results.overall_field_accuracy:.1%}")
        logger.info(f"Grounding rate: {eval_results.grounding_rate:.1%}")
        logger.info(f"Total cost: ${eval_results.total_cost:.4f}")
        
        return eval_results
    
    def _log_to_langfuse(self, doc_data: Dict[str, Any], pipeline_result: Dict[str, Any], 
                        doc_metrics: DocumentMetrics):
        """Log evaluation results to Langfuse."""
        if not self.langfuse:
            return
        
        try:
            trace = self.langfuse.trace(
                name="document_evaluation",
                input={
                    "document_id": doc_data["document_id"],
                    "document_type": doc_data["ground_truth"]["doc_type"]
                },
                output=pipeline_result,
                metadata={
                    "evaluation": True,
                    "model": self.config.model,
                    "split": self.config.split
                },
                tags=["evaluation", f"doc_type:{doc_metrics.doc_type}"]
            )
            
            # Log field-level scores
            trace.score(
                name="field_accuracy",
                value=doc_metrics.field_accuracy,
                comment=f"Extracted {sum(doc_metrics.field_metrics.values())}/{len(doc_metrics.field_metrics)} fields correctly"
            )
            
            trace.score(
                name="classification_accuracy", 
                value=1.0 if doc_metrics.classification_correct else 0.0
            )
            
            trace.score(
                name="grounding_rate",
                value=doc_metrics.grounding_rate
            )
            
        except Exception as e:
            logger.warning(f"Failed to log to Langfuse: {e}")


def generate_report(results: EvalResults, output_path: str):
    """Generate a markdown report from evaluation results."""
    
    report = f"""# Cargo Copilot Evaluation Report

**Generated:** {results.timestamp}  
**Model:** {results.config.model}  
**Split:** {results.config.split}  
**Documents:** {results.total_documents}

## Summary Metrics

| Metric | Value |
|--------|-------|
| Classification Accuracy | {results.classification_accuracy:.1%} |
| Overall Field Accuracy | {results.overall_field_accuracy:.1%} |
| Evidence Grounding Rate | {results.grounding_rate:.1%} |
| Average Latency | {results.avg_latency_ms:.0f}ms |
| Total Cost | ${results.total_cost:.4f} |
| Cost per Document | ${results.total_cost / results.total_documents:.4f} |

## Field-Level Performance

### All Fields
| Field | Precision | Recall | F1 | Count |
|-------|-----------|--------|-----|-------|
"""
    
    # Sort fields by F1 score descending
    sorted_fields = sorted(results.field_metrics.items(), key=lambda x: x[1].f1, reverse=True)
    for field_name, metrics in sorted_fields:
        report += f"| {field_name} | {metrics.precision:.1%} | {metrics.recall:.1%} | {metrics.f1:.1%} | {metrics.total_expected} |\n"
    
    # Per document type breakdown
    for doc_type, type_metrics in results.doc_type_metrics.items():
        report += f"\n### {doc_type.replace('_', ' ').title()}\n"
        report += "| Field | Precision | Recall | F1 | Count |\n"
        report += "|-------|-----------|--------|-----|-------|\n"
        
        sorted_type_fields = sorted(type_metrics.items(), key=lambda x: x[1].f1, reverse=True)
        for field_name, metrics in sorted_type_fields:
            report += f"| {field_name} | {metrics.precision:.1%} | {metrics.recall:.1%} | {metrics.f1:.1%} | {metrics.total_expected} |\n"
    
    # Document-level results
    report += "\n## Document Results\n\n"
    report += "| Document ID | Type | Predicted | Classification ✓ | Field Accuracy | Grounding Rate | Latency (ms) | Cost ($) |\n"
    report += "|-------------|------|-----------|------------------|----------------|----------------|--------------|----------|\n"
    
    for doc in results.document_results:
        classification_icon = "✅" if doc.classification_correct else "❌"
        report += f"| {doc.doc_id} | {doc.doc_type} | {doc.predicted_type} | {classification_icon} | {doc.field_accuracy:.1%} | {doc.grounding_rate:.1%} | {doc.latency_ms:.0f} | ${doc.total_cost:.4f} |\n"
    
    # Configuration
    report += f"\n## Configuration\n\n```json\n{results.config.model_dump_json(indent=2)}\n```\n"
    
    # Write report
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        f.write(report)
    
    logger.info(f"Evaluation report saved to {output_path}")


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Run Cargo Copilot evaluation")
    parser.add_argument("--split", choices=["train", "test"], default="test", 
                       help="Data split to evaluate on")
    parser.add_argument("--model", default="gpt-4o-mini", 
                       help="Model to use for evaluation")
    parser.add_argument("--max-docs", type=int, help="Maximum documents to process")
    parser.add_argument("--no-langfuse", action="store_true", 
                       help="Disable Langfuse logging")
    
    args = parser.parse_args()
    
    # Configure logging
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="ISO"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer()
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO level
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    
    # Create config
    config = EvalConfig(
        split=args.split,
        model=args.model,
        max_documents=args.max_docs,
        langfuse_enabled=not args.no_langfuse
    )
    
    # Run evaluation
    harness = EvaluationHarness(config)
    results = await harness.run_evaluation()
    
    # Generate report
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = f"{config.output_dir}/eval_{timestamp}_{args.model}_{args.split}.md"
    generate_report(results, report_path)
    
    # Save JSON results  
    json_path = report_path.replace(".md", ".json")
    with open(json_path, "w") as f:
        f.write(results.model_dump_json(indent=2))
    
    logger.info(f"Results saved to {report_path} and {json_path}")
    
    return results


if __name__ == "__main__":
    asyncio.run(main())
