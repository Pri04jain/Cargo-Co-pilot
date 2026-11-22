#!/usr/bin/env python3
"""
Evaluation results dashboard and visualization.

This script generates interactive visualizations from evaluation results:
- Field accuracy heatmaps by document type
- Cost and latency trends
- Confusion matrices for classification
- Grounding rate analysis
- Model comparison charts

Usage: python eval/dashboard.py --results eval/reports/eval_*.json
       python eval/dashboard.py --compare eval/reports/eval_gpt4*.json eval/reports/eval_claude*.json
"""

import argparse
import json
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from pathlib import Path
from typing import Dict, List, Any
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import plotly.offline as pyo

# Set style
plt.style.use('seaborn-v0_8')
sns.set_palette("husl")


class EvaluationDashboard:
    """Interactive dashboard for evaluation results."""
    
    def __init__(self, results_paths: List[str]):
        self.results = []
        self.results_paths = results_paths
        self._load_results()
    
    def _load_results(self):
        """Load evaluation results from JSON files."""
        for path in self.results_paths:
            try:
                with open(path) as f:
                    data = json.load(f)
                    # Add metadata
                    data['_file_path'] = path
                    data['_model_name'] = Path(path).stem.split('_')[2] if '_' in Path(path).stem else 'unknown'
                    self.results.append(data)
                print(f"Loaded results from {path}")
            except Exception as e:
                print(f"Failed to load {path}: {e}")
    
    def create_field_accuracy_heatmap(self) -> go.Figure:
        """Create heatmap of field accuracies by document type."""
        if not self.results:
            return go.Figure()
        
        # Collect field accuracy data
        data = []
        for result in self.results:
            model_name = result['_model_name']
            
            for doc_type, fields in result.get('doc_type_metrics', {}).items():
                for field_name, metrics in fields.items():
                    data.append({
                        'Model': model_name,
                        'Document Type': doc_type.replace('_', ' ').title(),
                        'Field': field_name,
                        'F1 Score': metrics['f1'],
                        'Precision': metrics['precision'],
                        'Recall': metrics['recall'],
                        'Count': metrics['total_expected']
                    })
        
        if not data:
            return go.Figure()
        
        df = pd.DataFrame(data)
        
        # Create pivot table for heatmap
        pivot = df.pivot_table(
            index=['Document Type', 'Field'], 
            columns='Model', 
            values='F1 Score', 
            aggfunc='mean'
        )
        
        fig = go.Figure(data=go.Heatmap(
            z=pivot.values,
            x=pivot.columns,
            y=[f"{idx[0]}<br>{idx[1]}" for idx in pivot.index],
            colorscale='RdYlGn',
            text=np.round(pivot.values, 3),
            texttemplate="%{text}",
            textfont={"size": 10},
            hoverongaps=False
        ))
        
        fig.update_layout(
            title="Field Extraction F1 Scores by Document Type and Model",
            xaxis_title="Model",
            yaxis_title="Document Type → Field",
            height=max(400, len(pivot.index) * 25)
        )
        
        return fig
    
    def create_cost_latency_scatter(self) -> go.Figure:
        """Create scatter plot of cost vs latency by model."""
        if not self.results:
            return go.Figure()
        
        fig = go.Figure()
        
        for result in self.results:
            model_name = result['_model_name']
            
            # Document-level data
            latencies = [doc['latency_ms'] for doc in result.get('document_results', [])]
            costs = [doc['total_cost'] for doc in result.get('document_results', [])]
            doc_types = [doc['doc_type'] for doc in result.get('document_results', [])]
            
            if latencies and costs:
                fig.add_trace(go.Scatter(
                    x=latencies,
                    y=costs,
                    mode='markers',
                    name=model_name,
                    text=[f"Type: {dt}" for dt in doc_types],
                    hovertemplate='<b>%{fullData.name}</b><br>' +
                                  'Latency: %{x:.0f}ms<br>' +
                                  'Cost: $%{y:.4f}<br>' +
                                  '%{text}<extra></extra>',
                    marker=dict(size=8, opacity=0.7)
                ))
        
        fig.update_layout(
            title="Cost vs Latency by Model",
            xaxis_title="Latency (ms)",
            yaxis_title="Cost ($)",
            hovermode='closest'
        )
        
        return fig
    
    def create_classification_accuracy_comparison(self) -> go.Figure:
        """Create bar chart comparing classification accuracy across models."""
        if not self.results:
            return go.Figure()
        
        models = []
        accuracies = []
        field_accuracies = []
        grounding_rates = []
        
        for result in self.results:
            models.append(result['_model_name'])
            accuracies.append(result.get('classification_accuracy', 0))
            field_accuracies.append(result.get('overall_field_accuracy', 0))
            grounding_rates.append(result.get('grounding_rate', 0))
        
        fig = go.Figure(data=[
            go.Bar(name='Classification Accuracy', x=models, y=accuracies),
            go.Bar(name='Field Accuracy', x=models, y=field_accuracies),
            go.Bar(name='Grounding Rate', x=models, y=grounding_rates)
        ])
        
        fig.update_layout(
            title="Model Performance Comparison",
            yaxis_title="Accuracy",
            barmode='group',
            yaxis=dict(tickformat='.1%')
        )
        
        return fig
    
    def create_cost_analysis_chart(self) -> go.Figure:
        """Create cost analysis breakdown."""
        if not self.results:
            return go.Figure()
        
        fig = make_subplots(
            rows=2, cols=2,
            subplot_titles=('Total Cost by Model', 'Cost per Document', 
                           'Latency Distribution', 'Cost vs Performance'),
            specs=[[{"type": "bar"}, {"type": "bar"}],
                   [{"type": "histogram"}, {"type": "scatter"}]]
        )
        
        for i, result in enumerate(self.results):
            model_name = result['_model_name']
            total_cost = result.get('total_cost', 0)
            doc_count = result.get('total_documents', 1)
            cost_per_doc = total_cost / doc_count if doc_count > 0 else 0
            field_accuracy = result.get('overall_field_accuracy', 0)
            
            # Total cost
            fig.add_trace(
                go.Bar(x=[model_name], y=[total_cost], name=f'{model_name} Total', 
                      showlegend=False, marker_color=px.colors.qualitative.Set1[i % len(px.colors.qualitative.Set1)]),
                row=1, col=1
            )
            
            # Cost per document
            fig.add_trace(
                go.Bar(x=[model_name], y=[cost_per_doc], name=f'{model_name} Per Doc',
                      showlegend=False, marker_color=px.colors.qualitative.Set1[i % len(px.colors.qualitative.Set1)]),
                row=1, col=2
            )
            
            # Latency distribution
            latencies = [doc['latency_ms'] for doc in result.get('document_results', [])]
            if latencies:
                fig.add_trace(
                    go.Histogram(x=latencies, name=f'{model_name} Latency', opacity=0.7),
                    row=2, col=1
                )
            
            # Cost vs Performance
            fig.add_trace(
                go.Scatter(x=[cost_per_doc], y=[field_accuracy], 
                          mode='markers+text', text=[model_name], 
                          textposition="top center", name=f'{model_name}',
                          marker=dict(size=12)),
                row=2, col=2
            )
        
        fig.update_layout(height=800, title_text="Cost Analysis Dashboard")
        fig.update_yaxes(title_text="Cost ($)", row=1, col=1)
        fig.update_yaxes(title_text="Cost per Doc ($)", row=1, col=2)
        fig.update_xaxes(title_text="Latency (ms)", row=2, col=1)
        fig.update_yaxes(title_text="Count", row=2, col=1)
        fig.update_xaxes(title_text="Cost per Doc ($)", row=2, col=2)
        fig.update_yaxes(title_text="Field Accuracy", row=2, col=2)
        
        return fig
    
    def create_field_performance_radar(self) -> go.Figure:
        """Create radar chart of field performance."""
        if not self.results:
            return go.Figure()
        
        fig = go.Figure()
        
        # Get all unique fields across all results
        all_fields = set()
        for result in self.results:
            for field_name in result.get('field_metrics', {}):
                all_fields.add(field_name)
        
        all_fields = sorted(list(all_fields))
        
        for result in self.results:
            model_name = result['_model_name']
            field_metrics = result.get('field_metrics', {})
            
            # Get F1 scores for each field
            f1_scores = []
            for field in all_fields:
                if field in field_metrics:
                    f1_scores.append(field_metrics[field]['f1'])
                else:
                    f1_scores.append(0.0)
            
            fig.add_trace(go.Scatterpolar(
                r=f1_scores,
                theta=all_fields,
                fill='toself',
                name=model_name,
                opacity=0.6
            ))
        
        fig.update_layout(
            polar=dict(
                radialaxis=dict(
                    visible=True,
                    range=[0, 1]
                )),
            title="Field Performance Radar Chart",
            height=600
        )
        
        return fig
    
    def generate_html_dashboard(self, output_path: str = "eval/reports/dashboard.html"):
        """Generate complete HTML dashboard."""
        print("Generating interactive dashboard...")
        
        # Create all plots
        plots = {
            'field_heatmap': self.create_field_accuracy_heatmap(),
            'cost_latency': self.create_cost_latency_scatter(),
            'classification_comparison': self.create_classification_accuracy_comparison(),
            'cost_analysis': self.create_cost_analysis_chart(),
            'field_radar': self.create_field_performance_radar()
        }
        
        # Generate HTML
        html_content = """
<!DOCTYPE html>
<html>
<head>
    <title>Cargo Copilot Evaluation Dashboard</title>
    <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; background-color: #f5f5f5; }
        .container { max-width: 1400px; margin: 0 auto; }
        .header { text-align: center; margin-bottom: 30px; padding: 20px; background: white; border-radius: 8px; }
        .plot-container { background: white; margin: 20px 0; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .metrics-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin: 20px 0; }
        .metric-card { background: white; padding: 15px; border-radius: 8px; text-align: center; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .metric-value { font-size: 24px; font-weight: bold; color: #2c3e50; }
        .metric-label { font-size: 14px; color: #7f8c8d; margin-top: 5px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🚢 Cargo Copilot Evaluation Dashboard</h1>
            <p>Interactive analysis of field extraction performance across models and document types</p>
        </div>
"""
        
        # Add summary metrics if we have results
        if self.results:
            html_content += '<div class="metrics-grid">'
            for result in self.results:
                model_name = result['_model_name']
                html_content += f"""
                <div class="metric-card">
                    <div class="metric-value">{result.get('classification_accuracy', 0):.1%}</div>
                    <div class="metric-label">{model_name}<br>Classification</div>
                </div>
                <div class="metric-card">
                    <div class="metric-value">{result.get('overall_field_accuracy', 0):.1%}</div>
                    <div class="metric-label">{model_name}<br>Field Accuracy</div>
                </div>
                <div class="metric-card">
                    <div class="metric-value">${result.get('total_cost', 0):.4f}</div>
                    <div class="metric-label">{model_name}<br>Total Cost</div>
                </div>
                """
            html_content += '</div>'
        
        # Add plots
        for plot_name, fig in plots.items():
            if fig.data:  # Only add non-empty plots
                html_content += f'<div class="plot-container"><div id="{plot_name}"></div></div>'
        
        html_content += """
    </div>
    <script>
"""
        
        # Add plot JavaScript
        for plot_name, fig in plots.items():
            if fig.data:
                config = {
                    'displayModeBar': True,
                    'displaylogo': False,
                    'modeBarButtonsToRemove': ['pan2d', 'lasso2d']
                }
                html_content += f"""
        var {plot_name}_data = {fig.to_json()};
        Plotly.newPlot('{plot_name}', {plot_name}_data.data, {plot_name}_data.layout, {config});
"""
        
        html_content += """
    </script>
</body>
</html>
"""
        
        # Write file
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w') as f:
            f.write(html_content)
        
        print(f"Dashboard saved to {output_path}")
        return output_path
    
    def generate_static_plots(self, output_dir: str = "eval/reports/plots"):
        """Generate static PNG plots for documentation."""
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        
        # Field accuracy heatmap
        if self.results:
            plt.figure(figsize=(12, 8))
            
            # Collect data for heatmap
            data = []
            for result in self.results:
                model_name = result['_model_name']
                for doc_type, fields in result.get('doc_type_metrics', {}).items():
                    for field_name, metrics in fields.items():
                        data.append({
                            'Model': model_name,
                            'Doc_Type': doc_type.replace('_', ' ').title(),
                            'Field': field_name,
                            'F1': metrics['f1']
                        })
            
            if data:
                df = pd.DataFrame(data)
                pivot = df.pivot_table(index='Field', columns=['Doc_Type', 'Model'], 
                                     values='F1', aggfunc='mean')
                
                sns.heatmap(pivot, annot=True, fmt='.2f', cmap='RdYlGn', 
                           center=0.5, vmin=0, vmax=1)
                plt.title('Field Extraction F1 Scores')
                plt.tight_layout()
                plt.savefig(f"{output_dir}/field_accuracy_heatmap.png", dpi=300, bbox_inches='tight')
                plt.close()
        
        print(f"Static plots saved to {output_dir}/")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Generate evaluation dashboard")
    parser.add_argument("results", nargs="+", help="Path(s) to evaluation JSON results")
    parser.add_argument("--output", default="eval/reports/dashboard.html", 
                       help="Output HTML file")
    parser.add_argument("--plots", help="Directory for static plots")
    
    args = parser.parse_args()
    
    # Create dashboard
    dashboard = EvaluationDashboard(args.results)
    
    # Generate HTML dashboard
    dashboard.generate_html_dashboard(args.output)
    
    # Generate static plots if requested
    if args.plots:
        dashboard.generate_static_plots(args.plots)
    
    print(f"\n✅ Dashboard generated successfully!")
    print(f"Open {args.output} in your browser to view the interactive dashboard")


if __name__ == "__main__":
    main()