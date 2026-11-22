# Labeled Test Data for Evaluation

This directory contains labeled ground truth data for evaluating the document processing pipeline.

## Structure

Each JSON file in this directory represents one test document with ground truth labels:

```
test/
├── bill_of_lading_001.json       # Labels for B/L document
├── commercial_invoice_001.json   # Labels for invoice
├── packing_list_001.json         # Labels for packing list
└── ...
```

## Label Format

Each JSON file must contain:

1. **document_id**: Unique identifier for this test case
2. **file_path**: Path to the actual PDF/image document (must exist)
3. **ground_truth**: Object containing:
   - **doc_type**: One of: `bill_of_lading`, `commercial_invoice`, `packing_list`
   - **fields**: Object mapping field names to their expected values and page numbers

### Example

```json
{
  "document_id": "test_bl_001",
  "file_path": "data/test_documents/my_bill_of_lading.pdf",
  "ground_truth": {
    "doc_type": "bill_of_lading",
    "fields": {
      "bl_number": {
        "value": "MAEU123456789",
        "page": 1
      },
      "vessel": {
        "value": "MSC OSCAR",
        "page": 1
      }
    }
  }
}
```

## Creating Test Data

### Step 1: Collect Real Documents

Gather actual shipping documents (PDFs or images) that you want to test:

- Bill of Lading documents
- Commercial Invoices  
- Packing Lists

Place them in a directory like `data/test_documents/` (or any location).

### Step 2: Manually Extract Ground Truth

For each document, manually read and extract the field values. This is the "correct answer" that the pipeline should produce.

### Step 3: Create JSON Labels

For each document, create a JSON file using the templates:

- `example_label_template.json` - Bill of Lading
- `example_invoice_template.json` - Commercial Invoice
- `example_packing_list_template.json` - Packing List

Replace the example values with the actual values from your document.

### Step 4: Verify File Paths

Ensure the `file_path` in each JSON points to an existing document:

```bash
# Check file exists
ls data/test_documents/my_document.pdf
```

## Field Names Reference

The field names must match the schema expected by the pipeline. See the templates for the complete list of supported fields for each document type.

### Bill of Lading Fields

- `bl_number`, `vessel`, `voyage_number`
- `port_of_loading`, `port_of_discharge`
- `shipper`, `consignee`, `notify_party` (nested: `name`, `address`)
- `container_numbers`, `cargo_description`
- `gross_weight`, `freight_terms`
- etc.

### Commercial Invoice Fields

- `invoice_number`, `invoice_date`
- `seller`, `buyer` (nested: `name`, `address`, `tax_id`)
- `currency`, `payment_terms`, `incoterms`
- `line_items`, `subtotal`, `total_amount`
- etc.

### Packing List Fields

- `packing_list_number`, `packing_list_date`
- `total_packages`, `packaging_type`
- `total_gross_weight`, `total_net_weight`, `total_volume`
- `items` (array of package details)
- `container_numbers`, `seal_numbers`
- etc.

## Nested Fields

Some fields are nested (e.g., `shipper.name`, `shipper.address`). Use nested objects:

```json
"shipper": {
  "name": {
    "value": "Company Name",
    "page": 1
  },
  "address": {
    "value": "123 Street, City",
    "page": 1
  }
}
```

## Page Numbers

The `page` field indicates which page of the document the value appears on (1-indexed).

This is used for:
- Evidence grounding validation
- Debugging extraction errors
- Analyzing per-page accuracy

## Tips for Creating Good Test Data

1. **Use real documents**: Actual shipping docs are better than synthetic ones
2. **Include variety**: Different formats, layouts, languages, quality levels
3. **Cover edge cases**: Missing fields, unusual formats, handwritten notes
4. **Label carefully**: Double-check values - these are the "correct answers"
5. **Start small**: 10-20 documents is enough to start; expand over time
6. **Balance document types**: Include mix of B/L, invoices, packing lists
7. **Document issues**: If a field is genuinely missing/illegible, mark as `null`

## Validation

Before running eval, validate your test data:

```bash
make eval-check
```

This checks:
- ✅ JSON files are valid
- ✅ All referenced document files exist
- ✅ Required fields are present
- ✅ Schema is correct

## Usage

Once you have test data in this directory:

```bash
# Run evaluation on test set
make eval

# Or with Python directly
docker compose exec api python eval/run_eval.py --split test
```

The eval will:
1. Load all JSON files from this directory
2. Upload each document to the API
3. Run the full pipeline
4. Compare results against your ground truth
5. Generate accuracy metrics

## Directory Structure Recommendation

```
data/
├── test_documents/          # Actual PDF/image files
│   ├── bl_001.pdf
│   ├── invoice_001.pdf
│   └── packing_001.pdf
└── synthetic/
    ├── test/                # Ground truth labels (this directory)
    │   ├── bl_001.json
    │   ├── invoice_001.json
    │   └── packing_001.json
    └── train/               # Optional: training/validation set
        └── ...
```

## Training vs Test Split

- **test/**: Held-out evaluation set (don't use for development)
- **train/**: Development/validation set (optional)

Use the test set only for final evaluation to avoid overfitting.

## Need Help?

See `eval/README.md` for complete evaluation documentation and troubleshooting.
