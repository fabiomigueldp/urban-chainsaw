# Trading Signal Processor

A high-performance trading signal processing system that filters and forwards trading signals based on real-time Finviz Top-N ticker rankings. Only signals for tickers currently in the top-N list are approved and forwarded to destination webhooks.

## 🏗️ System Architecture

```
┌─────────────┐  HTTP POST   ┌────────────────┐
│ Signal      │─────────────►│ /webhook/in    │
│ Source      │              │ (O(1) enqueue) │
└─────────────┘              └────────┬───────┘
                                      │ asyncio.Queue
                                      ▼
                             ┌─────────────────┐    ┌──────────────┐
                             │ Worker Pool     │    │ Finviz       │
                             │ • Top-N check   │◄───│ Engine       │
                             │ • Approve/Reject│    │ • Ticker     │
                             └─────────┬───────┘    │   Updates    │
                                       │            └──────────────┘
                                       ▼ (if approved)
                              ┌─────────────────┐
                              │ Forwarding      │
                              │ Workers         │
                              │ • Rate Limited  │
                              │ • HTTP Forward  │
                              └─────────┬───────┘
                                        │ HTTP POST
                                        ▼
                                ┌──────────────┐
                                │ Destination  │
                                │ Webhook      │
                                └──────────────┘
```

## 🚀 Quick Start

### Prerequisites

- Docker and Docker Compose
- Python 3.11+ (for local development)
- PostgreSQL database (provided via Docker Compose)

### Running the Application

The fastest way to get started is using the provided run script:

```bash
python run.py
```

This script will:
1. Check Docker and Docker Compose availability
2. Clean up any existing containers
3. Build and start the application stack
4. Verify application health
5. Display real-time logs

### Manual Docker Compose

Alternatively, you can run directly with Docker Compose:

```bash
# Start the application stack
docker-compose up --build

# Run in background
docker-compose up --build -d

# View logs
docker-compose logs -f trading-signal-processor

# Stop the application
docker-compose down
```

## ⚙️ Configuration

### Environment Variables

Create a `.env` file in the project root with the following configuration:

```env
# Required: Destination webhook URL
DEST_WEBHOOK_URL=https://your-webhook-destination.com/signals

# Signal Processing
TOP_N=15
FINVIZ_REFRESH_SEC=10
WORKER_CONCURRENCY=16
FORWARDING_WORKERS=5

# Database Configuration
DATABASE_URL=postgresql+asyncpg://postgres:postgres@postgres:5432/trading_signals
POSTGRES_PASSWORD=postgres123

# Rate Limiting
DEST_WEBHOOK_MAX_REQ_PER_MIN=60
DEST_WEBHOOK_RATE_LIMITING_ENABLED=true

# Optional: Finviz Elite (higher rate limits)
FINVIZ_USE_ELITE=false
FINVIZ_EMAIL=your-email@example.com
FINVIZ_PASSWORD=your-password

# Security
FINVIZ_UPDATE_TOKEN=your-secure-token

# Logging
LOG_LEVEL=INFO
```

### Dynamic Configuration

The Finviz configuration (URL, top_n, refresh rate) can be updated at runtime via:

- **Admin Interface**: http://localhost/admin
- **API Endpoint**: `POST /finviz/config`
- **Configuration File**: `finviz_config.json`

## 📡 API Endpoints

### Signal Processing

#### Receive Trading Signals
```http
POST /webhook/in
Content-Type: application/json

{
  "ticker": "AAPL",
  "side": "BUY",
  "price": 150.25,
  "time": "2024-01-01T10:00:00Z"
}
```

#### Manual Position Management
```http
POST /sell/individual
Content-Type: application/json

{
  "ticker": "AAPL",
  "token": "your-auth-token"
}
```

```http
POST /sell/all
Content-Type: application/json

{
  "token": "your-auth-token"
}
```

### Configuration Management

#### Update Finviz Configuration
```http
POST /finviz/config
Content-Type: application/json
Authorization: Bearer your-finviz-token

{
  "url": "https://finviz.com/screener.ashx?v=111&f=...",
  "top_n": 20,
  "refresh": 15
}
```

#### Get Current Configuration
```http
GET /finviz/config
```

### Monitoring

#### Health Check
```http
GET /health
```

#### System Status
```http
GET /status
```

#### Metrics
```http
GET /metrics
```

## 🖥️ Admin Interface

Access the web-based admin interface at: **http://localhost/admin**

Features:
- Real-time signal tracking and audit trails
- System metrics and performance monitoring
- Finviz configuration management
- Worker status and queue monitoring
- Interactive signal filtering and search

## 🔧 Development

### Local Development Setup

```bash
# Clone the repository
git clone <repository-url>
cd trading-signal-processor

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Set up environment variables
cp .env.example .env
# Edit .env with your configuration

# Start PostgreSQL (via Docker)
docker-compose up postgres -d

# Run the application
python -m uvicorn main:app --host 0.0.0.0 --port 80 --reload
```

### Project Structure

```
├── main.py                 # FastAPI application entry point
├── config.py              # Configuration management
├── models.py              # Pydantic data models
├── run.py                 # Application runner script
├── scanner.py             # Project documentation utility
├── comm_engine.py         # WebSocket communication system
├── finviz_engine.py       # Finviz scraping and ticker management
├── finviz.py              # HTML parsing and URL utilities
├── webhook_rate_limiter.py # Rate limiting for outbound requests
├── database/
│   ├── DBManager.py       # Database operations manager
│   ├── simple_models.py   # SQLAlchemy models
│   └── init.sql          # Database initialization
├── static/                # Web interface assets
├── templates/             # HTML templates
├── logs/                  # Application logs
├── docker-compose.yml     # Container orchestration
├── Dockerfile            # Container image definition
└── requirements.txt      # Python dependencies
```

### Key Components

- **FinvizEngine**: Manages real-time ticker list updates with rate limiting and Elite account support
- **Signal Processing Workers**: Asynchronous signal validation and processing
- **Forwarding Workers**: Rate-limited signal forwarding with retry logic
- **Communication Engine**: WebSocket-based real-time UI updates
- **Database Manager**: PostgreSQL-based signal tracking and audit trails

## 📊 Monitoring & Metrics

### Real-time Metrics

The system provides comprehensive metrics including:

- **Signal Metrics**: Received, approved, rejected, forwarded counts
- **Queue Status**: Processing and forwarding queue sizes
- **Worker Status**: Active worker counts and performance
- **Rate Limiting**: Current request rates and token availability
- **Database Analytics**: Historical signal statistics

### Logging

Structured logging with configurable levels:

```bash
# View real-time logs
docker-compose logs -f trading-signal-processor

# Filter by log level
docker-compose logs -f trading-signal-processor | grep ERROR
```

## 🔒 Security Considerations

- **Authentication**: Token-based authentication for configuration updates
- **Rate Limiting**: Built-in protection against rate limit violations
- **Input Validation**: Comprehensive Pydantic model validation
- **Database Security**: Parameterized queries and connection pooling
- **Network Security**: Internal communication between containers

## 🐛 Troubleshooting

### Common Issues

#### Application Won't Start
```bash
# Check Docker status
docker --version
docker-compose --version

# Verify environment variables
cat .env

# Check container logs
docker-compose logs trading-signal-processor
```

#### Database Connection Issues
```bash
# Check PostgreSQL status
docker-compose logs postgres

# Verify database connectivity
docker-compose exec postgres psql -U postgres -d trading_signals -c "\l"
```

#### Finviz Scraping Errors
- Verify network connectivity to finviz.com
- Check rate limiting configuration
- Validate Finviz Elite credentials (if applicable)
- Review finviz_config.json format

### Performance Tuning

#### High Signal Volume
- Increase `WORKER_CONCURRENCY`
- Adjust `QUEUE_MAX_SIZE`
- Scale database connection pool

#### Rate Limiting Issues
- Reduce `FINVIZ_REFRESH_SEC`
- Lower `TOP_N` value
- Consider Finviz Elite subscription

## 📈 Production Deployment

### Docker Deployment

```bash
# Production build
docker-compose -f docker-compose.yml -f docker-compose.prod.yml up --build -d

# Scale workers
docker-compose up --scale trading-signal-processor=3
```

### Environment-specific Configuration

Create environment-specific compose files:

- `docker-compose.dev.yml` - Development settings
- `docker-compose.staging.yml` - Staging configuration
- `docker-compose.prod.yml` - Production optimization

### Monitoring in Production

- Set up log aggregation (ELK stack, Fluentd)
- Configure health check alerts
- Monitor database performance
- Track signal processing latency

## 🤝 Support

For internal support:
1. Check the troubleshooting section above
2. Review application logs for error details
3. Consult the API documentation for endpoint usage
4. Use the admin interface for real-time debugging

## 📝 License

Internal use only. See company policies for usage guidelines.
