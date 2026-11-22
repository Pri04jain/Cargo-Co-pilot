# Cargo Copilot Evaluation Framework

This document describes the comprehensive evaluation system built for Phase 7 of Cargo Copilot, providing detailed analysis of field extraction performance, cost efficiency, and model comparison capabilities.

## 🎯 Overview

The evaluation framework measures system performance across multiple dimensions:

- **Field-Level Accuracy**: Precision, recall, and F1 scores for each extracted field
- **Evidence Grounding**: Success rate of linking extracted values to source document locations
- **Classification Performance**: Document type prediction accuracy
- **Cost Analysis**: Token usage, processing costs, and efficiency metrics
- **Latency Monitoring**: End-to-end processing time analysis
- **Model Comparison**: Side-by-side evaluation of different LLMs

## 🏗️ Architecture

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│  Test Data      │────│  Evaluation     │────│   Results       │
│  (Ground Truth) │    │  Harness        │    │   Storage       │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │
         │              ┌─────────────────┐    ┌─────────────────┐
         └──────────────│  Pipeline API   │────│   Langfuse      │
                        │  (Processing)   │    │ (Cost Tracking) │
                        └─────────────────┘    └─────────────────┘
                                 │                       │
                        ┌─────────────────┐    ┌─────────────────┐
                        │  Dashboard      │────│  Visualization  │
                        │  Generator      │    │   & Reports     │
                        └─────────────────┘    └─────────────────┘
```

## 📊 Evaluation Metrics

### Field-Level Accuracy

For each document field, we measure:

- **Precision**: `correct_extractions / total_extractions`
- **Recall**: `correct_extractions / total_expected`
- **F1 Score**: `2 * (precision * recall) / (precision + recall)`

Field accuracy is calculated by exact string match after normalization (lowercasing, whitespace trimming).

### Evidence Grounding Rate

Measures how successfully the system links extracted values to source locations:

```python
grounding_rate = grounded_fields / total_extracted_fields
```

A field is considered "grounded" when:
1. The extracted value matches ground truth
2. The evidence snippet is found in the document
3. The bounding box coordinates are valid

### Classification Accuracy

Document type prediction success rate:

```python
classification_accuracy = correct_predictions / total_documents
```

### Cost Efficiency Metrics

- **Cost per Document**: Average processing cost per document
- **Cost per Field**: Average cost per extracted field  
- **Cost per Correct Field**: Cost efficiency considering accuracy
- **Accuracy per Dollar**: Accuracy points achieved per dollar spent
- **Efficiency Score**: Composite metric (0-100) combining accuracy, cost, and latency

## 🔧 Usage

### Basic Evaluation

```bash
# Run evaluation on test set with default model
make eval

# View results
ls eval/reports/
cat eval/reports/eval_*.md
```

### Advanced Evaluation

```bash
# Multi-model comparison
make eval-full

# Generate interactive dashboard  
make eval-dashboard

# Cost analysis with Langfuse
make eval-cost

# Baseline benchmarking
make eval-baseline
```

### Custom Evaluation

```bash
cd eval

# Specific model and dataset size
python run_eval.py --split test --model gpt-4o --max-docs 100

# Custom output location
python run_eval.py --split test --model claude-3-sonnet --output-dir custom_results

# Disable Langfuse logging
python run_eval.py --split test --no-langfuse
```

### Dashboard Generation

```bash
# Generate from specific results
python eval/dashboard.py eval/reports/eval_20241208_123456_gpt-4o-mini_test.json

# Compare multiple models
python eval/dashboard.py eval/reports/eval_*gpt-4o*.json eval/reports/eval_*claude*.json

# Custom dashboard output
python eval/dashboard.py eval/reports/eval_*.json --output custom_dashboard.html
```

## 📈 Sample Results Analysis

### Performance Summary

```
📊 Evaluation Results Summary (Test Set: N=50)
==============================================
Model: gpt-4o-mini
Classification Accuracy: 94.0%
Overall Field Accuracy:  87.3%
Evidence Grounding Rate: 91.2%
Average Latency:         2,340ms
Total Cost:             $1.23
Cost per Document:      $0.025

Performance by Document Type:
────────────────────────────
Bill of Lading (20 docs):    89% field accuracy
Commercial Invoice (20 docs): 86% field accuracy  
Packing List (10 docs):      85% field accuracy
```

### Field-Level Analysis

| Field | Precision | Recall | F1 | Notes |
|-------|-----------|--------|-----|-------|
| bl_number | 98% | 98% | 98% | ✅ Excellent |
| invoice_number | 95% | 95% | 95% | ✅ Excellent |
| vessel | 92% | 89% | 90% | ✅ Good |
| total_amount | 88% | 85% | 86% | ⚠️ Needs improvement |
| packages_count | 76% | 74% | 75% | ❌ Poor performance |
| dimensions | 68% | 65% | 66% | ❌ Poor performance |

### Cost Analysis

```
💰 Cost Breakdown by Processing Step
===================================
Classification:   $0.005 (20%)
Field Extraction: $0.015 (60%)  
Evidence Grounding: $0.004 (15%)
Validation:       $0.001 (5%)
Total per Doc:    $0.025

Optimization Recommendations:
─────────────────────────────
1. Use gpt-3.5-turbo for classification (4x cost reduction)
2. Implement field-specific prompts for complex extractions
3. Cache validation rules to reduce repeated LLM calls
4. Consider fine-tuning a smaller model on high-volume fields
```

## 🎨 Interactive Dashboard

The evaluation dashboard provides comprehensive visualizations:

### Performance Heatmaps
- Field accuracy by document type and model
- Color-coded performance indicators
- Drill-down capabilities for detailed analysis

### Cost vs Accuracy Analysis
- Scatter plots showing efficiency trade-offs
- Model comparison charts
- Cost optimization opportunities

### Latency Distribution  
- Processing time histograms
- Performance percentiles
- Bottleneck identification

### Field Performance Radar
- Multi-dimensional performance visualization
- Model comparison overlays
- Strengths and weaknesses identification

### Access Dashboard

```bash
# Generate and open dashboard
make eval-dashboard
open eval/reports/dashboard.html

# Or custom generation
python eval/dashboard.py eval/reports/eval_*.json
```

## 💰 Cost Tracking with Langfuse

### Setup Langfuse Integration

1. **Create Langfuse Account**: Sign up at [cloud.langfuse.com](https://cloud.langfuse.com)

2. **Get API Keys**: Create a project and copy your keys

3. **Configure Environment**:
   ```bash
   # Add to .env
   LANGFUSE_PUBLIC_KEY=pk_your_public_key
   LANGFUSE_SECRET_KEY=sk_your_secret_key
   LANGFUSE_HOST=https://cloud.langfuse.com
   ```

### Cost Tracking Features

```python
from eval.langfuse_tracker import LangfuseCostTracker

# Initialize tracker
tracker = LangfuseCostTracker()

# Track document processing
cost_breakdown = await tracker.track_document_processing(
    doc_data, pipeline_result, metrics
)

# Generate cost report
report = await tracker.generate_cost_report(session_results)
```

### Real-Time Monitoring

Langfuse provides:
- **Live Cost Tracking**: Real-time token usage and costs
- **Performance Metrics**: Latency and accuracy monitoring
- **Trace Analysis**: Detailed execution traces
- **Alerting**: Cost threshold notifications
- **Optimization Insights**: Usage pattern analysis

Access your dashboard: `https://cloud.langfuse.com/project/{project_id}/traces`

## 🧪 Testing Framework

### Test Data Structure

```
data/synthetic/
├── train/                 # Training documents (30 docs)
│   ├── doc_001.json      # Ground truth labels
│   ├── doc_001.pdf       # Source document
│   └── ...
├── test/                  # Held-out test set (20 docs)
│   ├── doc_031.json
│   ├── doc_031.pdf
│   └── ...
└── generate.py           # Synthetic data generator
```

### Ground Truth Format

```json
{
  "document_id": "test_bl_001",
  "file_path": "data/synthetic/test/test_bl_001.pdf",
  "ground_truth": {
    "doc_type": "bill_of_lading",
    "fields": {
      "bl_number": {
        "value": "ABC123456789",
        "page": 1,
        "bbox": [100, 200, 300, 220]
      },
      "shipper": {
        "name": {
          "value": "Global Exports Ltd",
          "page": 1,
          "bbox": [50, 150, 250, 180]
        },
        "address": {
          "value": "123 Export St, Shanghai",
          "page": 1,
          "bbox": [50, 185, 300, 205]
        }
      }
    }
  }
}
```

### Adding Test Cases

1. **Create Ground Truth**:
   ```bash
   # Add new test document
   cp new_document.pdf data/synthetic/test/
   
   # Create ground truth JSON
   cat > data/synthetic/test/new_document.json << EOF
   {
     "document_id": "test_new_001",
     "ground_truth": { ... }
   }
   EOF
   ```

2. **Validate Format**:
   ```bash
   python -c "
   import json
   with open('data/synthetic/test/new_document.json') as f:
     data = json.load(f)
   print('✅ Valid JSON format')
   "
   ```

3. **Run Evaluation**:
   ```bash
   make eval
   ```

## 🔍 Troubleshooting

### Common Issues

**No evaluation results found**
```bash
# Ensure test data exists
ls data/synthetic/test/

# Check for JSON format errors
python -m json.tool data/synthetic/test/doc_001.json

# Generate demo data if needed
python eval/demo.py
```

**Langfuse connection failed**
```bash
# Verify API keys
echo $LANGFUSE_PUBLIC_KEY
echo $LANGFUSE_SECRET_KEY

# Test connection
python -c "from langfuse import Langfuse; Langfuse().get_traces(limit=1)"

# Run without Langfuse
python eval/run_eval.py --no-langfuse
```

**Dashboard generation failed**
```bash
# Install visualization dependencies
pip install -r eval/requirements.txt

# Check results files exist
ls eval/reports/eval_*.json

# Generate with verbose output
python eval/dashboard.py eval/reports/eval_*.json --verbose
```

### Performance Issues

**Slow evaluation runs**
```bash
# Limit document count
python eval/run_eval.py --max-docs 10

# Use faster model for testing
python eval/run_eval.py --model gpt-3.5-turbo

# Run in parallel (future enhancement)
# Currently evaluations are sequential
```

**High memory usage**
```bash
# Monitor memory during evaluation
docker stats

# Reduce batch size (future enhancement)
# Currently processes one document at a time
```

## 🚀 Future Enhancements

### Planned Features

1. **Parallel Processing**: Concurrent document evaluation
2. **A/B Testing**: Automated model comparison workflows
3. **Regression Testing**: Automated evaluation on code changes
4. **Custom Metrics**: Domain-specific evaluation criteria
5. **Export Formats**: Excel, CSV, and API exports
6. **Real-time Monitoring**: Live evaluation dashboards
7. **Alerting**: Performance degradation notifications

### Contributing

To add new evaluation features:

1. **Extend Metrics**: Add new metrics to `EvalResults` class
2. **Update Harness**: Modify `EvaluationHarness.evaluate_document()`
3. **Add Visualizations**: Create new dashboard components
4. **Write Tests**: Add unit tests for new functionality
5. **Update Documentation**: Document new features

Example: Adding a new metric

```python
# In eval/run_eval.py
class EvalResults(BaseModel):
    # ... existing fields ...
    custom_metric: float = 0.0

# In evaluate_document method
def evaluate_document(self, ...):
    # ... existing logic ...
    custom_value = calculate_custom_metric(doc_data, pipeline_result)
    return DocumentMetrics(
        # ... existing fields ...
        custom_metric=custom_value
    )
```

## 📚 References

- **LangGraph Documentation**: [langchain-ai.github.io/langgraph](https://langchain-ai.github.io/langgraph/)
- **Langfuse Documentation**: [langfuse.com/docs](https://langfuse.com/docs)
- **Plotly Documentation**: [plotly.com/python](https://plotly.com/python/)
- **Evaluation Best Practices**: [ml-evaluation-best-practices.md](ml-evaluation-best-practices.md)

---

The evaluation framework provides comprehensive insights into Cargo Copilot's performance, enabling data-driven optimization and confident production deployment.