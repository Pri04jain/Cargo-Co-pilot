# Cargo Copilot Deployment Guide

This guide covers production deployment of the Cargo Copilot shipping document processing system.

## 🏗️ Architecture Overview

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Load Balancer │────│   Frontend      │    │   Langfuse      │
│   (nginx/ALB)   │    │   (React SPA)   │    │   (Observability)│
└─────────────────┘    └─────────────────┘    └─────────────────┘
                                │                       │
                       ┌─────────────────┐    ┌─────────────────┐
                       │   Backend API   │────│   PostgreSQL    │
                       │   (FastAPI)     │    │   + pgvector    │
                       └─────────────────┘    └─────────────────┘
                                │                       │
                       ┌─────────────────┐    ┌─────────────────┐
                       │   Celery Queue  │────│      Redis      │
                       │   (Workers)     │    │   (Cache/Queue) │
                       └─────────────────┘    └─────────────────┘
                                │
                       ┌─────────────────┐
                       │   File Storage  │
                       │   (S3/MinIO)    │
                       └─────────────────┘
```

## 🛠️ Infrastructure Requirements

### Minimum Production Requirements

| Component | CPU | RAM | Storage | Notes |
|-----------|-----|-----|---------|-------|
| Backend API | 2 cores | 4GB | 20GB SSD | Scales horizontally |
| Celery Workers | 2 cores | 8GB | 10GB SSD | CPU intensive (PDF processing) |
| PostgreSQL | 2 cores | 4GB | 100GB SSD | Vector storage requires SSD |
| Redis | 1 core | 2GB | 10GB | Session storage + task queue |
| Frontend | 1 core | 1GB | 5GB | Served via CDN recommended |

### Recommended Production Setup

- **API Servers**: 3+ instances behind load balancer
- **Workers**: 5-10 instances depending on document volume  
- **Database**: Primary + read replica with pgvector extension
- **Storage**: S3-compatible object storage for documents
- **Monitoring**: Langfuse + application metrics (Prometheus/Grafana)

## 🐳 Docker Deployment

### Docker Compose (Development/Small Production)

```bash
# Start all services
make up

# Scale workers for higher throughput
docker compose up -d --scale worker=5

# Check service health
docker compose ps
docker compose logs -f api
```

### Production Docker Compose

Create `docker-compose.prod.yml`:

```yaml
version: '3.8'

services:
  api:
    image: cargo-copilot-api:latest
    environment:
      - DATABASE_URL=postgresql://user:pass@db:5432/cargo_copilot
      - REDIS_URL=redis://redis:6379
      - LANGFUSE_PUBLIC_KEY=${LANGFUSE_PUBLIC_KEY}
      - LANGFUSE_SECRET_KEY=${LANGFUSE_SECRET_KEY}
      - AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID}
      - AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY}
      - S3_BUCKET=${S3_BUCKET}
    deploy:
      replicas: 3
      resources:
        limits:
          cpus: '2.0'
          memory: 4G
        reservations:
          cpus: '1.0'
          memory: 2G
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s

  worker:
    image: cargo-copilot-api:latest
    command: celery -A app.workers worker --loglevel=info --concurrency=4
    environment:
      - DATABASE_URL=postgresql://user:pass@db:5432/cargo_copilot
      - REDIS_URL=redis://redis:6379
      - LANGFUSE_PUBLIC_KEY=${LANGFUSE_PUBLIC_KEY}
      - LANGFUSE_SECRET_KEY=${LANGFUSE_SECRET_KEY}
    deploy:
      replicas: 5
      resources:
        limits:
          cpus: '2.0'
          memory: 8G

  frontend:
    image: cargo-copilot-frontend:latest
    environment:
      - REACT_APP_API_URL=https://api.cargo-copilot.com
    deploy:
      replicas: 2

  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf
      - ./ssl:/etc/ssl/certs
    depends_on:
      - api
      - frontend
```

## ☁️ Kubernetes Deployment

### Namespace and ConfigMap

```yaml
# k8s/namespace.yaml
apiVersion: v1
kind: Namespace
metadata:
  name: cargo-copilot

---
# k8s/configmap.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: cargo-copilot-config
  namespace: cargo-copilot
data:
  DATABASE_URL: "postgresql://user:pass@postgres-service:5432/cargo_copilot"
  REDIS_URL: "redis://redis-service:6379"
  S3_BUCKET: "cargo-copilot-documents"
  LOG_LEVEL: "INFO"
```

### API Deployment

```yaml
# k8s/api-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: cargo-copilot-api
  namespace: cargo-copilot
spec:
  replicas: 3
  selector:
    matchLabels:
      app: cargo-copilot-api
  template:
    metadata:
      labels:
        app: cargo-copilot-api
    spec:
      containers:
      - name: api
        image: cargo-copilot-api:latest
        ports:
        - containerPort: 8000
        env:
        - name: DATABASE_URL
          valueFrom:
            configMapKeyRef:
              name: cargo-copilot-config
              key: DATABASE_URL
        - name: LANGFUSE_PUBLIC_KEY
          valueFrom:
            secretKeyRef:
              name: cargo-copilot-secrets
              key: langfuse-public-key
        resources:
          requests:
            cpu: 1000m
            memory: 2Gi
          limits:
            cpu: 2000m
            memory: 4Gi
        livenessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 30
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /ready
            port: 8000
          initialDelaySeconds: 10
          periodSeconds: 5

---
apiVersion: v1
kind: Service
metadata:
  name: cargo-copilot-api-service
  namespace: cargo-copilot
spec:
  selector:
    app: cargo-copilot-api
  ports:
  - port: 80
    targetPort: 8000
  type: ClusterIP
```

### Worker Deployment

```yaml
# k8s/worker-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: cargo-copilot-worker
  namespace: cargo-copilot
spec:
  replicas: 5
  selector:
    matchLabels:
      app: cargo-copilot-worker
  template:
    metadata:
      labels:
        app: cargo-copilot-worker
    spec:
      containers:
      - name: worker
        image: cargo-copilot-api:latest
        command: ["celery", "-A", "app.workers", "worker", "--loglevel=info", "--concurrency=4"]
        envFrom:
        - configMapRef:
            name: cargo-copilot-config
        - secretRef:
            name: cargo-copilot-secrets
        resources:
          requests:
            cpu: 1000m
            memory: 4Gi
          limits:
            cpu: 2000m
            memory: 8Gi
```

### Ingress Configuration

```yaml
# k8s/ingress.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: cargo-copilot-ingress
  namespace: cargo-copilot
  annotations:
    kubernetes.io/ingress.class: "nginx"
    cert-manager.io/cluster-issuer: "letsencrypt-prod"
    nginx.ingress.kubernetes.io/client-max-body-size: "50m"
spec:
  tls:
  - hosts:
    - api.cargo-copilot.com
    - app.cargo-copilot.com
    secretName: cargo-copilot-tls
  rules:
  - host: api.cargo-copilot.com
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: cargo-copilot-api-service
            port:
              number: 80
  - host: app.cargo-copilot.com
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: cargo-copilot-frontend-service
            port:
              number: 80
```

## 🗄️ Database Setup

### PostgreSQL with pgvector

```sql
-- Create database
CREATE DATABASE cargo_copilot;

-- Connect to database
\c cargo_copilot;

-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Create user with appropriate permissions
CREATE USER cargo_copilot_user WITH PASSWORD 'secure_password';
GRANT ALL PRIVILEGES ON DATABASE cargo_copilot TO cargo_copilot_user;
GRANT USAGE ON SCHEMA public TO cargo_copilot_user;
GRANT CREATE ON SCHEMA public TO cargo_copilot_user;
```

### Database Migration

```bash
# Run Alembic migrations
docker exec -it cargo-copilot-api alembic upgrade head

# Or in Kubernetes
kubectl exec -it deployment/cargo-copilot-api -n cargo-copilot -- alembic upgrade head
```

### Database Backup Strategy

```bash
#!/bin/bash
# backup.sh - Daily database backup script

DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="cargo_copilot_backup_${DATE}.sql"

# Create backup
pg_dump -h $DB_HOST -U $DB_USER -d cargo_copilot > $BACKUP_FILE

# Compress and upload to S3
gzip $BACKUP_FILE
aws s3 cp ${BACKUP_FILE}.gz s3://cargo-copilot-backups/

# Keep local backups for 7 days
find . -name "cargo_copilot_backup_*.sql.gz" -mtime +7 -delete

echo "Backup completed: ${BACKUP_FILE}.gz"
```

## 🔐 Security Configuration

### Environment Variables

Create `.env.prod`:

```bash
# Database
DATABASE_URL=postgresql://user:password@host:5432/cargo_copilot

# Redis
REDIS_URL=redis://redis-host:6379

# JWT
SECRET_KEY=your-secret-key-at-least-32-characters-long
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30

# LLM APIs
OPENAI_API_KEY=sk-your-openai-key
ANTHROPIC_API_KEY=your-anthropic-key

# Langfuse
LANGFUSE_PUBLIC_KEY=pk_your_public_key
LANGFUSE_SECRET_KEY=sk_your_secret_key
LANGFUSE_HOST=https://cloud.langfuse.com

# AWS S3
AWS_ACCESS_KEY_ID=your-access-key
AWS_SECRET_ACCESS_KEY=your-secret-key
S3_BUCKET=cargo-copilot-documents
S3_REGION=us-east-1

# Security
ALLOWED_HOSTS=["api.cargo-copilot.com", "localhost"]
CORS_ORIGINS=["https://app.cargo-copilot.com"]
```

### SSL/TLS Configuration

```nginx
# nginx.conf
server {
    listen 443 ssl http2;
    server_name api.cargo-copilot.com;

    ssl_certificate /etc/ssl/certs/api.cargo-copilot.com.pem;
    ssl_certificate_key /etc/ssl/private/api.cargo-copilot.com.key;
    
    # Modern SSL configuration
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-RSA-AES256-GCM-SHA512:DHE-RSA-AES256-GCM-SHA512:ECDHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off;
    
    # Security headers
    add_header Strict-Transport-Security "max-age=63072000" always;
    add_header X-Frame-Options DENY;
    add_header X-Content-Type-Options nosniff;
    
    # File upload size
    client_max_body_size 50M;
    
    location / {
        proxy_pass http://api-backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Timeout settings
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }
}

upstream api-backend {
    server cargo-copilot-api-1:8000;
    server cargo-copilot-api-2:8000;
    server cargo-copilot-api-3:8000;
}
```

## 📊 Monitoring and Observability

### Health Checks

Add to `backend/app/api/health.py`:

```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
import redis
import os

router = APIRouter()

@router.get("/health")
async def health_check(db: AsyncSession = Depends(get_db)):
    """Basic health check endpoint."""
    try:
        # Check database
        await db.execute("SELECT 1")
        
        # Check Redis
        r = redis.from_url(os.getenv("REDIS_URL"))
        r.ping()
        
        return {"status": "healthy", "timestamp": "2024-01-01T00:00:00Z"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Service unhealthy: {e}")

@router.get("/ready")
async def readiness_check(db: AsyncSession = Depends(get_db)):
    """Readiness check for Kubernetes."""
    # Similar checks but more comprehensive
    return {"status": "ready"}
```

### Prometheus Metrics

```python
# backend/app/monitoring/metrics.py
from prometheus_client import Counter, Histogram, Gauge, generate_latest
import time

# Metrics
DOCUMENT_PROCESSING_TOTAL = Counter(
    'document_processing_total', 
    'Total documents processed',
    ['doc_type', 'status']
)

DOCUMENT_PROCESSING_DURATION = Histogram(
    'document_processing_duration_seconds',
    'Time spent processing documents',
    ['doc_type']
)

EXTRACTION_ACCURACY = Gauge(
    'extraction_accuracy_ratio',
    'Field extraction accuracy',
    ['doc_type', 'field']
)

PROCESSING_COST = Histogram(
    'processing_cost_dollars',
    'Cost per document processing',
    ['model']
)

def track_document_processing(doc_type: str, start_time: float, success: bool):
    """Track document processing metrics."""
    duration = time.time() - start_time
    
    DOCUMENT_PROCESSING_TOTAL.labels(
        doc_type=doc_type,
        status='success' if success else 'error'
    ).inc()
    
    DOCUMENT_PROCESSING_DURATION.labels(doc_type=doc_type).observe(duration)
```

### Grafana Dashboard

Create `monitoring/grafana-dashboard.json`:

```json
{
  "dashboard": {
    "title": "Cargo Copilot Monitoring",
    "panels": [
      {
        "title": "Processing Rate",
        "type": "graph",
        "targets": [
          {
            "expr": "rate(document_processing_total[5m])",
            "legendFormat": "Documents/sec"
          }
        ]
      },
      {
        "title": "Accuracy by Document Type", 
        "type": "table",
        "targets": [
          {
            "expr": "avg by (doc_type) (extraction_accuracy_ratio)",
            "legendFormat": "{{doc_type}}"
          }
        ]
      },
      {
        "title": "Processing Cost",
        "type": "graph", 
        "targets": [
          {
            "expr": "avg(processing_cost_dollars) by (model)",
            "legendFormat": "{{model}}"
          }
        ]
      }
    ]
  }
}
```

## 🚀 Deployment Checklist

### Pre-Deployment

- [ ] Environment variables configured
- [ ] SSL certificates installed
- [ ] Database migrations tested
- [ ] S3 bucket permissions configured
- [ ] Langfuse project set up
- [ ] Load testing completed
- [ ] Security scan passed
- [ ] Backup strategy implemented

### Deployment Steps

```bash
# 1. Build and tag images
docker build -t cargo-copilot-api:v1.0.0 backend/
docker build -t cargo-copilot-frontend:v1.0.0 frontend/

# 2. Push to registry
docker tag cargo-copilot-api:v1.0.0 your-registry/cargo-copilot-api:v1.0.0
docker push your-registry/cargo-copilot-api:v1.0.0

# 3. Deploy to Kubernetes
kubectl apply -f k8s/

# 4. Verify deployment
kubectl get pods -n cargo-copilot
kubectl logs -f deployment/cargo-copilot-api -n cargo-copilot

# 5. Run health checks
curl https://api.cargo-copilot.com/health
```

### Post-Deployment

- [ ] Health endpoints responding
- [ ] Metrics being collected
- [ ] Alerts configured
- [ ] Documentation updated
- [ ] Team notified
- [ ] Monitoring dashboards accessible

## 🔧 Scaling and Performance

### Horizontal Scaling

```bash
# Scale API servers
kubectl scale deployment cargo-copilot-api --replicas=5 -n cargo-copilot

# Scale workers based on queue size
kubectl scale deployment cargo-copilot-worker --replicas=10 -n cargo-copilot

# Auto-scaling with HPA
kubectl apply -f k8s/hpa.yaml
```

### Performance Tuning

1. **Database Optimization**
   - Enable connection pooling
   - Add appropriate indexes
   - Configure read replicas

2. **Caching Strategy**
   - Redis for session data
   - CDN for frontend assets
   - API response caching

3. **Resource Optimization**
   - Profile memory usage
   - Optimize Docker images
   - Use multi-stage builds

## 🆘 Troubleshooting

### Common Issues

**High Memory Usage in Workers**
```bash
# Check memory usage
kubectl top pods -n cargo-copilot

# Restart workers
kubectl rollout restart deployment/cargo-copilot-worker -n cargo-copilot
```

**Database Connection Issues**
```bash
# Check database connectivity
kubectl exec -it deployment/cargo-copilot-api -n cargo-copilot -- psql $DATABASE_URL

# Check connection pool
kubectl logs deployment/cargo-copilot-api -n cargo-copilot | grep "pool"
```

**High API Latency**
```bash
# Check processing times in Langfuse
# Scale up workers if queue is backing up
kubectl get pods -n cargo-copilot | grep worker
```

### Maintenance Windows

```bash
# Graceful shutdown for maintenance
kubectl scale deployment cargo-copilot-api --replicas=1 -n cargo-copilot
kubectl drain node-name --ignore-daemonsets --delete-local-data

# Database maintenance
kubectl exec -it postgres-pod -- pg_dump cargo_copilot > backup.sql
kubectl exec -it postgres-pod -- psql cargo_copilot < maintenance.sql
```

This deployment guide provides a comprehensive foundation for running Cargo Copilot in production. Adjust configurations based on your specific infrastructure and requirements.