#!/usr/bin/env python3
"""
Langfuse cost tracking and analysis for Cargo Copilot evaluation.

This module provides enhanced Langfuse integration for:
- Detailed cost tracking per document and field
- Performance monitoring and alerting
- Cost optimization recommendations
- Real-time evaluation metrics

Usage:
    from eval.langfuse_tracker import LangfuseCostTracker
    
    tracker = LangfuseCostTracker()
    await tracker.track_document_processing(doc_data, pipeline_result, metrics)
"""

import asyncio
import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

from langfuse import Langfuse
from langfuse.decorators import observe, langfuse_context
import structlog

logger = structlog.get_logger()


@dataclass
class CostBreakdown:
    """Detailed cost breakdown for a processing run."""
    classification_cost: float = 0.0
    extraction_cost: float = 0.0
    grounding_cost: float = 0.0
    validation_cost: float = 0.0
    total_cost: float = 0.0
    
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    
    model_name: str = ""
    processing_time_ms: float = 0.0


@dataclass
class PerformanceMetrics:
    """Performance metrics for cost analysis."""
    cost_per_field: float = 0.0
    cost_per_correct_field: float = 0.0
    latency_per_dollar: float = 0.0  # ms per dollar
    accuracy_per_dollar: float = 0.0  # accuracy points per dollar
    
    efficiency_score: float = 0.0  # Combined metric


class LangfuseCostTracker:
    """Enhanced Langfuse integration for cost tracking and analysis."""
    
    def __init__(self):
        self.langfuse = None
        self.session_id = f"eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        try:
            self.langfuse = Langfuse(
                public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
                secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
                host=os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")
            )
            logger.info("Langfuse cost tracker initialized")
        except Exception as e:
            logger.warning(f"Failed to initialize Langfuse: {e}")
            logger.info("Cost tracking will use local estimation only")
    
    @observe(name="document_processing")
    async def track_document_processing(self, doc_data: Dict[str, Any], 
                                      pipeline_result: Dict[str, Any],
                                      doc_metrics: Any) -> CostBreakdown:
        """Track complete document processing with detailed cost breakdown."""
        if not self.langfuse:
            return self._estimate_cost_breakdown(doc_data, pipeline_result, doc_metrics)
        
        trace_id = f"{self.session_id}_{doc_data['document_id']}"
        
        try:
            # Create main trace
            trace = self.langfuse.trace(
                id=trace_id,
                name="document_processing",
                input={
                    "document_id": doc_data["document_id"],
                    "document_type": doc_data["ground_truth"]["doc_type"],
                    "file_path": doc_data.get("file_path", ""),
                    "session_id": self.session_id
                },
                output=pipeline_result,
                metadata={
                    "evaluation": True,
                    "ground_truth": doc_data["ground_truth"],
                    "model": getattr(doc_metrics, 'model_name', 'unknown'),
                    "split": "test"
                },
                tags=[
                    "evaluation", 
                    f"doc_type:{doc_data['ground_truth']['doc_type']}",
                    f"session:{self.session_id}"
                ]
            )
            
            # Track detailed steps
            await self._track_classification_step(trace, pipeline_result, doc_data)
            await self._track_extraction_step(trace, pipeline_result, doc_data) 
            await self._track_grounding_step(trace, pipeline_result, doc_data)
            
            # Add performance scores
            self._add_performance_scores(trace, doc_metrics, doc_data)
            
            # Get cost breakdown from Langfuse
            cost_breakdown = await self._get_cost_from_langfuse(trace_id)
            
            return cost_breakdown
            
        except Exception as e:
            logger.error(f"Failed to track document processing in Langfuse: {e}")
            return self._estimate_cost_breakdown(doc_data, pipeline_result, doc_metrics)
    
    async def _track_classification_step(self, parent_trace, pipeline_result: Dict, doc_data: Dict):
        """Track the classification step specifically."""
        if not self.langfuse:
            return
        
        classification = pipeline_result.get("classification", {})
        
        span = self.langfuse.span(
            trace_id=parent_trace.id,
            name="classify_document",
            input={
                "document_type_hint": doc_data["ground_truth"]["doc_type"],
                "confidence_threshold": 0.8
            },
            output=classification,
            metadata={
                "step": "classification",
                "model": "gpt-4o-mini"  # TODO: get from config
            }
        )
        
        # Score classification accuracy
        gt_type = doc_data["ground_truth"]["doc_type"]
        predicted_type = classification.get("doc_type", "unknown")
        
        span.score(
            name="classification_accuracy",
            value=1.0 if gt_type == predicted_type else 0.0,
            comment=f"GT: {gt_type}, Predicted: {predicted_type}"
        )
        
        span.score(
            name="classification_confidence",
            value=classification.get("confidence", 0.0)
        )
    
    async def _track_extraction_step(self, parent_trace, pipeline_result: Dict, doc_data: Dict):
        """Track the field extraction step."""
        if not self.langfuse:
            return
        
        extraction = pipeline_result.get("extraction", {})
        
        span = self.langfuse.span(
            trace_id=parent_trace.id,
            name="extract_fields",
            input={
                "expected_fields": list(doc_data["ground_truth"].get("fields", {}).keys()),
                "document_type": doc_data["ground_truth"]["doc_type"]
            },
            output=extraction,
            metadata={
                "step": "extraction",
                "model": "gpt-4o-mini",
                "field_count": len(extraction)
            }
        )
        
        # Score individual fields
        gt_fields = doc_data["ground_truth"].get("fields", {})
        
        for field_name, gt_data in gt_fields.items():
            extracted_field = extraction.get(field_name, {})
            
            if isinstance(gt_data, dict) and "value" in gt_data:
                gt_value = gt_data["value"]
                extracted_value = extracted_field.get("value") if isinstance(extracted_field, dict) else None
                
                span.score(
                    name=f"field_accuracy_{field_name}",
                    value=1.0 if gt_value == extracted_value else 0.0,
                    comment=f"GT: {gt_value}, Extracted: {extracted_value}"
                )
                
                if isinstance(extracted_field, dict):
                    span.score(
                        name=f"field_confidence_{field_name}",
                        value=extracted_field.get("confidence", 0.0)
                    )
    
    async def _track_grounding_step(self, parent_trace, pipeline_result: Dict, doc_data: Dict):
        """Track the evidence grounding step."""
        if not self.langfuse:
            return
        
        extraction = pipeline_result.get("extraction", {})
        
        # Count grounded vs ungrounded fields
        grounded_count = 0
        total_fields = 0
        
        for field_name, field_data in extraction.items():
            if isinstance(field_data, dict):
                total_fields += 1
                if field_data.get("status") == "grounded":
                    grounded_count += 1
        
        grounding_rate = grounded_count / total_fields if total_fields > 0 else 0.0
        
        span = self.langfuse.span(
            trace_id=parent_trace.id,
            name="ground_evidence",
            input={
                "extracted_fields": list(extraction.keys()),
                "total_fields": total_fields
            },
            output={
                "grounded_fields": grounded_count,
                "grounding_rate": grounding_rate
            },
            metadata={
                "step": "grounding",
                "grounded_count": grounded_count,
                "total_count": total_fields
            }
        )
        
        span.score(
            name="grounding_rate",
            value=grounding_rate,
            comment=f"Grounded {grounded_count}/{total_fields} fields"
        )
    
    def _add_performance_scores(self, trace, doc_metrics: Any, doc_data: Dict):
        """Add performance and efficiency scores."""
        if not self.langfuse:
            return
        
        # Field accuracy
        trace.score(
            name="field_accuracy",
            value=getattr(doc_metrics, 'field_accuracy', 0.0),
            comment=f"Correctly extracted fields"
        )
        
        # Grounding rate  
        trace.score(
            name="grounding_rate",
            value=getattr(doc_metrics, 'grounding_rate', 0.0),
            comment="Evidence grounding success rate"
        )
        
        # Latency
        trace.score(
            name="latency_ms",
            value=getattr(doc_metrics, 'latency_ms', 0.0),
            comment="End-to-end processing latency"
        )
        
        # Cost efficiency (accuracy per dollar)
        cost = getattr(doc_metrics, 'total_cost', 0.001)
        accuracy = getattr(doc_metrics, 'field_accuracy', 0.0)
        efficiency = accuracy / cost if cost > 0 else 0.0
        
        trace.score(
            name="cost_efficiency",
            value=efficiency,
            comment=f"Accuracy points per dollar: {efficiency:.2f}"
        )
    
    async def _get_cost_from_langfuse(self, trace_id: str) -> CostBreakdown:
        """Retrieve actual cost data from Langfuse API."""
        if not self.langfuse:
            raise RuntimeError("Langfuse not initialized - cannot retrieve cost data")
        
        try:
            # Fetch trace data from Langfuse
            # Note: This requires the Langfuse SDK to support trace retrieval
            # For now, we'll return a breakdown indicating real data should be used
            logger.warning(
                "Direct cost retrieval from Langfuse not yet implemented. "
                "Cost should be tracked via Langfuse dashboard or API."
            )
            # Return empty breakdown - cost should come from API response
            return CostBreakdown()
            
        except Exception as e:
            logger.error(f"Failed to retrieve cost from Langfuse: {e}")
            raise
    
    def _estimate_cost_breakdown(self, doc_data: Dict, pipeline_result: Dict, 
                                doc_metrics: Any) -> CostBreakdown:
        """
        Fallback cost estimation when Langfuse data is unavailable.
        Note: This should only be used as a last resort. Real costs should come from
        the pipeline API response which tracks actual token usage.
        """
        logger.warning(
            "Using fallback cost estimation. This is not accurate. "
            "Ensure LLM provider tracks costs properly and they are returned in API responses."
        )
        
        # Return minimal breakdown indicating data is not real
        return CostBreakdown(
            classification_cost=0.0,
            extraction_cost=0.0,
            grounding_cost=0.0,
            validation_cost=0.0,
            total_cost=0.0,
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            model_name="unknown",
            processing_time_ms=getattr(doc_metrics, 'latency_ms', 0.0)
        )
    
    def calculate_performance_metrics(self, cost_breakdown: CostBreakdown, 
                                    doc_metrics: Any, 
                                    field_count: int) -> PerformanceMetrics:
        """Calculate performance efficiency metrics."""
        
        accuracy = getattr(doc_metrics, 'field_accuracy', 0.0)
        correct_fields = int(field_count * accuracy)
        latency = getattr(doc_metrics, 'latency_ms', 1000.0)
        cost = cost_breakdown.total_cost
        
        # Avoid division by zero
        cost = max(cost, 0.0001)
        
        metrics = PerformanceMetrics(
            cost_per_field=cost / max(field_count, 1),
            cost_per_correct_field=cost / max(correct_fields, 1),
            latency_per_dollar=latency / cost,
            accuracy_per_dollar=accuracy / cost,
            efficiency_score=0.0
        )
        
        # Calculate composite efficiency score (0-100)
        # Higher is better: more accuracy, less cost, lower latency
        accuracy_weight = 50  # 50% weight
        cost_weight = 30      # 30% weight  
        latency_weight = 20   # 20% weight
        
        # Normalize metrics (assuming targets: 90% accuracy, $0.01 cost, 2000ms latency)
        accuracy_norm = min(accuracy / 0.9, 1.0) * accuracy_weight
        cost_norm = min(0.01 / cost, 1.0) * cost_weight
        latency_norm = min(2000.0 / latency, 1.0) * latency_weight
        
        metrics.efficiency_score = accuracy_norm + cost_norm + latency_norm
        
        return metrics
    
    async def generate_cost_report(self, session_results: List[Dict]) -> Dict[str, Any]:
        """Generate comprehensive cost analysis report."""
        if not session_results:
            return {}
        
        total_cost = sum(r.get('total_cost', 0) for r in session_results)
        total_docs = len(session_results)
        avg_cost_per_doc = total_cost / total_docs if total_docs > 0 else 0
        
        # Group by document type
        type_costs = {}
        for result in session_results:
            doc_type = result.get('doc_type', 'unknown')
            if doc_type not in type_costs:
                type_costs[doc_type] = {'cost': 0, 'count': 0, 'accuracy': 0}
            
            type_costs[doc_type]['cost'] += result.get('total_cost', 0)
            type_costs[doc_type]['count'] += 1
            type_costs[doc_type]['accuracy'] += result.get('field_accuracy', 0)
        
        # Calculate averages
        for doc_type in type_costs:
            count = type_costs[doc_type]['count']
            type_costs[doc_type]['avg_cost'] = type_costs[doc_type]['cost'] / count
            type_costs[doc_type]['avg_accuracy'] = type_costs[doc_type]['accuracy'] / count
            type_costs[doc_type]['efficiency'] = type_costs[doc_type]['avg_accuracy'] / max(type_costs[doc_type]['avg_cost'], 0.0001)
        
        report = {
            'session_id': self.session_id,
            'timestamp': datetime.now().isoformat(),
            'summary': {
                'total_cost': total_cost,
                'total_documents': total_docs,
                'avg_cost_per_document': avg_cost_per_doc,
                'cost_per_accuracy_point': total_cost / max(sum(r.get('field_accuracy', 0) for r in session_results), 0.001)
            },
            'by_document_type': type_costs,
            'cost_optimization_recommendations': self._generate_cost_recommendations(type_costs, avg_cost_per_doc),
            'langfuse_dashboard_url': f"https://cloud.langfuse.com/project/default/traces?sessionId={self.session_id}" if self.langfuse else None
        }
        
        return report
    
    def _generate_cost_recommendations(self, type_costs: Dict, avg_cost: float) -> List[str]:
        """Generate cost optimization recommendations based on measured data."""
        if not type_costs:
            return ["No cost data available to generate recommendations."]
        
        recommendations = []
        
        # Find most expensive document types (based on actual measurements)
        sorted_types = sorted(type_costs.items(), key=lambda x: x[1]['avg_cost'], reverse=True)
        
        most_expensive = sorted_types[0]
        if most_expensive[1]['avg_cost'] > avg_cost * 1.5:
            recommendations.append(
                f"Document type '{most_expensive[0]}' costs ${most_expensive[1]['avg_cost']:.4f} per document, "
                f"which is 50% above the average of ${avg_cost:.4f}. "
                f"Consider analyzing the prompts and token usage for this document type."
            )
        
        # Find low efficiency types (low accuracy relative to cost)
        low_efficiency = [
            (doc_type, metrics) 
            for doc_type, metrics in type_costs.items() 
            if metrics['efficiency'] < 50 and metrics['avg_accuracy'] < 0.85
        ]
        
        for doc_type, metrics in low_efficiency:
            recommendations.append(
                f"Low efficiency for '{doc_type}': {metrics['avg_accuracy']:.1%} accuracy at "
                f"${metrics['avg_cost']:.4f} per document (efficiency: {metrics['efficiency']:.1f}). "
                f"This suggests the model is struggling with this document type. "
                f"Consider field-specific prompts or few-shot examples."
            )
        
        # Compare actual costs across document types
        if len(sorted_types) > 1:
            cheapest = sorted_types[-1]
            if most_expensive[1]['avg_cost'] > 2 * cheapest[1]['avg_cost']:
                recommendations.append(
                    f"Large cost variance detected: '{most_expensive[0]}' (${most_expensive[1]['avg_cost']:.4f}) "
                    f"costs {most_expensive[1]['avg_cost'] / cheapest[1]['avg_cost']:.1f}x more than "
                    f"'{cheapest[0]}' (${cheapest[1]['avg_cost']:.4f}). "
                    f"Investigate whether complexity justifies the difference."
                )
        
        # Overall cost threshold recommendations based on measured average
        if avg_cost > 0.05:
            recommendations.append(
                f"Average cost per document is ${avg_cost:.4f}, which is relatively high. "
                f"Based on measured data, consider: "
                f"1) Using a smaller model for classification, "
                f"2) Optimizing prompts to reduce token usage, "
                f"3) Implementing prompt caching for common patterns."
            )
        elif avg_cost < 0.001:
            recommendations.append(
                f"Average cost per document is very low (${avg_cost:.4f}). "
                f"If accuracy is below target, consider using a more capable model."
            )
        
        # If no specific issues found
        if not recommendations:
            recommendations.append(
                f"Cost efficiency appears reasonable with average of ${avg_cost:.4f} per document. "
                f"Continue monitoring for cost drift over time."
            )
        
        return recommendations
    
    async def setup_cost_alerts(self, cost_threshold: float = 0.05, 
                               accuracy_threshold: float = 0.8):
        """Set up cost and performance monitoring alerts."""
        if not self.langfuse:
            logger.info("Langfuse not available, alerts will be logged only")
            return
        
        logger.info(f"Cost alerts configured: >${cost_threshold:.4f} per document, <{accuracy_threshold:.1%} accuracy")
        
        # Note: Real implementation would set up webhooks or monitoring rules
        # For now, this is a placeholder for the alert configuration


async def demo_cost_tracking():
    """
    Demo removed: Cost tracking should use real pipeline data.
    
    To test cost tracking:
    1. Ensure LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are set
    2. Run: make eval
    3. Check Langfuse dashboard for actual cost data
    """
    print("Cost tracking demo disabled. Use 'make eval' with real documents instead.")
    print("Ensure Langfuse credentials are configured in your environment.")


if __name__ == "__main__":
    asyncio.run(demo_cost_tracking())