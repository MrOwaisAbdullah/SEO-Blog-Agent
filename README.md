# ContentSpark AI - SEO Blog Agent

Automated SEO-optimized blog post generation system that creates 15-17 high-quality posts daily for a Next.js website hosted on Vercel with Sanity CMS.

## Table of Contents

- [Overview](#overview)
- [System Architecture](#system-architecture)
- [Prerequisites](#prerequisites)
- [Environment Variables](#environment-variables)
- [Installation](#installation)
- [Setup Instructions](#setup-instructions)
- [API Endpoints](#api-endpoints)
- [Workflow](#workflow)
- [Troubleshooting](#troubleshooting)
- [License](#license)

## Overview

ContentSpark AI is an automated system designed to generate 15–17 high-quality, SEO-optimized blog posts daily for a SaaS platform focused on automated social media content creation and scheduling. The system uses a multi-agent workflow orchestrated by the OpenAI Agents SDK with free-tier APIs and fallbacks for reliability.

## System Architecture

The system consists of multiple agents working together:

1. **Triage Agent** - Selects keywords or YouTube URLs for research
2. **Research Agent** - Conducts keyword research using Tavily API
3. **Brief Agent** - Creates content briefs from research findings
4. **Content Generator Agent** - Generates SEO-optimized blog posts
5. **Posting Agent** - Publishes content to Sanity CMS
6. **Repurposing Agent** - Repurposes content for other platforms (planned)

## Prerequisites

- Python 3.8+
- Google Cloud Platform account (for Google Sheets API)
- Sanity CMS account
- API keys for various services (see Environment Variables section)
- Google Service Account credentials (JSON format)

## Environment Variables

Create a `.env` file in the root directory with the following variables:

```env
# LLM API Keys
GEMINI_API_KEY=your_gemini_api_key
OPENROUTER_API_KEY=your_openrouter_api_key
OPENAI_API_KEY=your_openai_api_key

# Sanity CMS Configuration
SANITY_PROJECT_ID=your_sanity_project_id
SANITY_API_TOKEN=your_sanity_api_token
SANITY_DATASET=production
SANITY_DEFAULT_AUTHOR_ID=your_author_document_id
SANITY_DEFAULT_AUTHOR_NAME="Your Author Name"

# Google Sheets Credentials
GOOGLE_CREDENTIALS={"type":"service_account","project_id":"your_project","private_key_id":"your_key_id","private_key":"-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n","client_email":"your_service_account_email","client_id":"your_client_id","auth_uri":"https://accounts.google.com/o/oauth2/auth","token_uri":"https://oauth2.googleapis.com/token","auth_provider_x509_cert_url":"https://www.googleapis.com/oauth2/v1/certs","client_x509_cert_url":"your_cert_url"}

# Search and Research API Keys
TAVILY_API_KEY=your_tavily_api_key
SERPAPI_KEY=your_serpapi_key

# Image API Keys
PEXELS_API_KEY=your_pexels_api_key
CLOUDFLARE_ACCOUNT_ID=your_cloudflare_account_id
CLOUDFLARE_API_TOKEN=your_cloudflare_workers_ai_token

# Security
API_KEY=your_custom_api_key_for_authentication

# Optional: Override default model settings
DEFAULT_MODEL=gemini-2.5-flash
```

### Required Environment Variables Breakdown

#### LLM API Keys
- `GEMINI_API_KEY` - Google Gemini API key for primary LLM
- `OPENROUTER_API_KEY` - OpenRouter API key for fallback LLMs
- `OPENAI_API_KEY` - OpenAI API key for fallback LLMs

#### Sanity CMS Configuration
- `SANITY_PROJECT_ID` - Your Sanity project ID
- `SANITY_API_TOKEN` - API token with write permissions
- `SANITY_DATASET` - Dataset name (usually "production")
- `SANITY_DEFAULT_AUTHOR_ID` - Document ID of the author to attribute posts to
- `SANITY_DEFAULT_AUTHOR_NAME` - Name of the default author

#### Google Sheets Credentials
- `GOOGLE_CREDENTIALS` - JSON string of Google Service Account credentials (contents of your service account key file)
  
  _Note: This should contain the entire JSON content from your Google Service Account key file, not a file path. The service account key file (e.g., `contentspark-service-account-key.json`) should be kept secure and never committed to version control._

#### Search and Research API Keys
- `TAVILY_API_KEY` - Tavily API key for web search and research
- `SERPAPI_KEY` - SerpAPI key for search data (fallback)

#### Image API Keys
- `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN` - Cloudflare Workers AI for AI image generation (primary; genuinely free, 10,000 Neurons/day)
- `PEXELS_API_KEY` - Pexels API key for stock images (fallback)

#### Security
- `API_KEY` - Custom API key for authenticating requests to the agent API

## Installation

1. **Clone the repository:**
   ```bash
   git clone <repository-url>
   cd seo-blog-agent
   ```

2. **Install dependencies (this project uses [uv](https://docs.astral.sh/uv/), not pip):**
   ```bash
   uv sync
   ```
   This creates and manages `.venv` automatically from `pyproject.toml`/`uv.lock` —
   no separate manual venv step needed. Run project commands via `uv run ...`
   (e.g. `uv run uvicorn main:app --reload`) or activate `.venv` directly.

3. **Create `.env` file:**
   Copy `.env.example` to `.env` and fill in your actual values (see
   `docs/service_setup.md` for where to get each credential).

## Setup Instructions

### 1. Google Sheets Setup

1. Create a Google Sheet named "ContentSpark" with the following worksheets:
   - `ContentSpark_Keywords` - For input keywords/YouTube URLs
   - `research_data` - For research findings
   - `content_briefs` - For content briefs
   - `generated_posts` - For generated blog posts

2. Create a Google Cloud Project and enable the Google Sheets API

3. Create a Service Account:
   - Go to Google Cloud Console → IAM & Admin → Service Accounts
   - Create a new service account
   - Download the JSON key file (e.g., `contentspark-service-account-key.json`)
   - Keep this file secure and never commit it to version control

4. Configure Google Sheets Access:
   - Open your Google Sheet
   - Click "Share" button
   - Add the service account email address from your JSON key file as an editor
   - The email will look like: `contentspark-service-account@your-project.iam.gserviceaccount.com`

### 2. Sanity CMS Setup

1. Create a Sanity project at https://sanity.io

2. Deploy the Sanity Studio to manage content

3. Create the required document types (post, author, category) using the provided schema

4. Create at least one author document and note its document ID

5. Obtain an API token with write permissions

### 3. API Keys Setup

Obtain API keys for all the required services and add them to your `.env` file.

### 4. Testing the Setup

1. **Start the API server:**
   ```bash
   uv run uvicorn main:app --reload
   ```
   Note: as of this repo's move to GitHub Actions + Discord for triggering
   (see `docs/service_setup.md`), `main.py` is no longer the active way the
   pipeline runs day-to-day — this is just for local testing of the
   endpoints, which are still present in the code.

2. **Test the health endpoint:**
   ```bash
   curl http://localhost:8000/health
   ```

3. **Test with authentication:**
   ```bash
   curl -H "Authorization: Bearer your_api_key" http://localhost:8000/health
   ```

## API Endpoints

### Health Check
```
GET /health
```
Response: `{"status": "OK", "message": "The server is healthy"}`

### Research Topic
```
GET /research
Headers: Authorization: Bearer <your_api_key>
```
Triggers the research workflow to select a keyword and conduct research.

### Generate Content Brief
```
GET /generate_brief
Headers: Authorization: Bearer <your_api_key>
```
Generates a content brief from approved research findings.

### Generate Content
```
GET /generate_content
Headers: Authorization: Bearer <your_api_key>
```
Creates a blog post from an approved content brief.

### Post Content
```
GET /post_content
Headers: Authorization: Bearer <your_api_key>
```
Publishes an approved blog post to Sanity CMS.

## Workflow

The system follows a multi-step workflow:

1. **Triage** - Selects the next keyword or YouTube URL from the ContentSpark_Keywords sheet
2. **Research** - Conducts research on the selected topic using Tavily API
3. **Brief Creation** - Creates a content brief from research findings
4. **Content Generation** - Generates a full blog post from the brief
5. **Posting** - Publishes the blog post to Sanity CMS
6. **Repurposing** - (Planned) Repurposes content for other platforms

Each step is handled by a dedicated agent with built-in fallback logic across different LLM providers.

## Security Best Practices

1. **Environment Variables**:
   - Never commit `.env` files to version control
   - Use different API keys for development, staging, and production environments
   - Rotate API keys regularly

2. **Google Service Account**:
   - Keep the service account key file (`contentspark-service-account-key.json`) secure
   - Limit the service account's permissions to only what's necessary
   - Use a dedicated service account for this application

3. **Sanity CMS**:
   - Use API tokens with minimal required permissions
   - Rotate tokens regularly
   - Monitor API usage

4. **API Keys**:
   - Enable rate limiting where possible
   - Use API key restrictions (IP whitelisting, referrer restrictions)
   - Monitor usage for unusual activity

5. **Application Security**:
   - Use the `API_KEY` environment variable to protect your endpoints
   - Keep dependencies updated
   - Regularly audit third-party services

## Troubleshooting

### Common Issues

1. **Authentication Errors**
   - Verify your `API_KEY` in the `.env` file
   - Ensure you're using the correct Bearer token format

2. **Google Sheets Access Errors**
   - Check that your service account has access to the spreadsheet
   - Verify the `GOOGLE_CREDENTIALS` JSON is correctly formatted
   - Ensure the sheet names match exactly

3. **Sanity CMS Errors**
   - Verify `SANITY_PROJECT_ID` and `SANITY_API_TOKEN` are correct
   - Check that `SANITY_DEFAULT_AUTHOR_ID` exists in your Sanity project
   - Ensure your Sanity schema matches the expected structure

4. **API Rate Limits**
   - The system includes fallback logic across providers
   - Check the custom runner implementation for quota management

### Logs and Debugging

Enable verbose logging by uncommenting the following line in `blog_agent/blog_agents.py`:
```python
# enable_verbose_stdout_logging()
```

### Environment Verification

Check that all environment variables are loaded correctly:
```bash
python -c "import os; from dotenv import load_dotenv; load_dotenv(); [print(f'{k}: {v[:10]}...') for k, v in os.environ.items() if 'KEY' in k or 'TOKEN' in k or 'API' in k]"
```

## License

This project is licensed under the MIT License - see the LICENSE file for details.