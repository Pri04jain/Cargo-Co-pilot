# Cargo Copilot Evaluation System

This evaluation system runs the **real pipeline** with the **real LLM API** on labeled documents and computes metrics from actual outputs.

## ⚠️ Important: No Simulation

This eval system does NOT use:
- ❌ Simulated results
- ❌ Hardcoded metrics
- ❌ Fake numbers
- ❌ Estimated costs

It DOES use:
- ✅ Real document processing pipeline
- ✅ Actual LLM API calls
- ✅ Measured token usage and costs
- ✅ Ground truth comparison

## Prerequisites

### 1. Labeled Test Data

Create labeled documents in `data/synthetic/test/` (or `train/`). Each JSON file should reference a real document and include ground truth labels:

```json
{
  "document_id": "test_bl_001",
  "file_path": "data/test_documents/bill_of_lading_001.pdf",
  "ground_truth": {
    "doc_type": "bill_of_lading",
    "fields": {
      "bl_number": {"value": "MAEU123456789", "page": 1},
      "vessel": {"value": "MSC OSCAR", "page": 1},
      "shipper": {
        "name": {"value": "Global Exports Ltd", "page": 1}
      },
      "port_of_loading": {"value": "Shanghai", "page": 1},
      "port_of_discharge": {"value": "Los Angeles", "page": 1}
    }
  }
}
```

**Important:** The `file_path` must point to an actual PDF or image file that exists.

### 2. Environment Variables

Set these environment variables before running eval:

```bash
# LLM API credentials (required)
export LLM_API_KEY="sk-..."           # OpenAI API key or other provider
export LLM_PROVIDER="openai"          # Default: openai

# API authentication (required)
export EVAL_API_TOKEN="..."           # JWT token for API access
                                       # Get this by logging into the app or creating a service account

# Langfuse (optional, for cost tracking)
export LANGFUSE_PUBLIC_KEY="pk-..."
export LANGFUSE_SECRET_KEY="sk-..."
export LANGFUSE_HOST="https://cloud.langfuse.com"
```

### 3. Running API

The backend API must be running:

```bash
docker compose up -d
```

## Usage

### Basic Evaluation

Run evaluation on the test set:

```bash
make eval
```

This will:
1. ✅ Validate prerequisites (API keys, API connectivity, labeled data)
2. ✅ Upload each test document to the API
3. ✅ Run the full pipeline (parse → classify → extract → ground → validate → route)
4. ✅ Compare results against ground truth
5. ✅ Generate a report in `eval/reports/`

### Advanced Options

```bash
# Run on training set instead of test set
docker compose exec api python eval/run_eval.py --split train

# Use a different model
docker compose exec api python eval/run_eval.py --model gpt-4o

# Limit number of documents (for quick testing)
docker compose exec api python eval/run_eval.py --max-docs 10

# Disable Langfuse logging
docker compose exec api python eval/run_eval.py --no-langfuse
```

### Full Model Comparison

Compare multiple models:

```bash
make eval-full
```

This runs the eval with both `gpt-4o-mini` and `gpt-4o` and generates comparative reports.

## Output

### Reports

Evaluation generates two files per run:

1. **Markdown Report** (`eval/reports/eval_TIMESTAMP_MODEL_SPLIT.md`)
   - Summary metrics (classification accuracy, field accuracy, grounding rate)
   - Per-field performance breakdown
   - Per-document results table
   - Cost analysis

2. **JSON Data** (`eval/reports/eval_TIMESTAMP_MODEL_SPLIT.json`)
   - Complete structured results
   - Used by dashboard generation
   - Machine-readable for further analysis

### Metrics Computed

All metrics are computed from **actual pipeline outputs**:

- **Classification Accuracy**: % of documents classified correctly
- **Field Accuracy**: % of fields extracted with correct values
- **Grounding Rate**: % of fields with valid evidence citations
- **Latency**: Actual end-to-end processing time per document
- **Cost**: Real token usage and API costs (from LLM provider)

### Cost Tracking

When Langfuse is enabled, detailed cost tracking is available:

- Per-document costs
- Per-step costs (classify, extract, ground, validate)
- Token usage breakdown (input/output)
- Cost efficiency metrics (accuracy per dollar)

View in Langfuse dashboard or check the JSON report.

## Troubleshooting

### "EVAL_API_TOKEN not set"

You need to authenticate with the API. Options:

1. **Create a test user and get a token:**
   ```bash
   # Start the app
   make up
   
   # Create a test user via the UI or API
   # Login and copy the JWT token from the browser dev tools
   export EVAL_API_TOKEN="eyJ..."
   ```

2. **Or use a service account** (if implemented)

### "Test data directory does not exist"

Create labeled test data:

```bash
mkdir -p data/synthetic/test
# Add your labeled JSON files here
```

### "LLM_API_KEY not set"

Set your OpenAI API key (or other provider):

```bash
export LLM_API_KEY="sk-..."
```

### "Cannot connect to API"

Ensure the backend is running:

```bash
docker compose up -d
docker compose ps  # Check status
docker compose logs api  # Check logs
```

### "Document file not found"

The `file_path` in your labeled JSON must point to an actual file:

```bash
# Check file exists
ls data/test_documents/your_file.pdf

# Update the JSON file_path if needed
```

## Architecture

The eval harness:

1. **Loads labeled data** from `data/synthetic/{split}/`
2. **Validates prerequisites** (API keys, connectivity, files exist)
3. **For each document:**
   - Uploads file to `/api/v1/documents/upload`
   - Polls `/api/v1/documents/{id}/status` until complete
   - Fetches extraction results from `/api/v1/documents/{id}/fields`
   - Compares against ground truth
   - Records metrics (accuracy, latency, cost)
4. **Aggregates results** across all documents
5. **Generates reports** with field-level and document-level breakdown

This ensures eval reflects real-world performance, not simulated behavior.

## Cost Optimization

After running eval, check the report for cost recommendations based on **measured data**:

- Document types with unusually high costs
- Low accuracy-to-cost ratios
- Token usage patterns
- Model comparison (gpt-4o vs gpt-4o-mini)

All recommendations are based on actual measured costs from your eval run.
