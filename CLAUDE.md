# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Python application that streams real-time stock news from Alpaca Markets WebSocket API and sends filtered notifications to Discord and Mastodon. The bot focuses on S&P 1500 companies and filters for earnings reports and analyst rating changes from approved sources like Benzinga Newsdesk.

## Development Commands

### Core Commands
- `uv run pytest` - Run all tests with coverage reporting
- `uv run ruff format --check` - Check code formatting (lint)
- `uv run pyright src/*` - Run type checking
- `make all` - Run lint, tests, and type checking together
- `make test` - Run tests only
- `make lint` - Check formatting only
- `make typecheck` - Run type checking only

### Running the Application
- `uv run run_bot.py` - Start the news streaming bot
- `docker compose up` - Run with Docker (includes Redis dependency)

### Dependencies
- `uv sync` - Install/sync dependencies
- `uv add <package>` - Add new dependency

## Architecture

### Core Components

**news_realtime.py** - Main application entry point that:
- Connects to Alpaca WebSocket API for real-time news streaming
- Authenticates and subscribes to news feeds
- Filters news by approved authors (Benzinga Newsdesk), single symbols, and US exchanges only
- Routes different news types to appropriate handlers

**notify.py** - Notification system that sends formatted messages to:
- Discord webhooks (separate channels for earnings, analyst ratings, general news)
- Mastodon social media posts
- Uses rich embeds with stock logos and color-coded notifications

**utils.py** - Utility functions for:
- Redis caching to prevent duplicate notifications
- Earnings data parsing using regex patterns
- S&P 1500 stock list management from Wikipedia sources
- Company name extraction and data validation

**analyst.py** - Analyst rating parser that extracts structured data from headlines:
- Parses firm names, actions (upgrades/downgrades), price targets
- Uses regex patterns to identify rating changes and target adjustments

**config.py** - Pydantic settings management for:
- API keys (Alpaca, Discord webhooks, Mastodon tokens)
- Redis connection settings
- Stock filtering and notification preferences

### Data Flow
1. WebSocket receives news from Alpaca API
2. Messages filtered by symbol count, exchange, author, and S&P 1500 membership
3. Redis cache prevents duplicate processing
4. News categorized as earnings, analyst ratings, or general news
5. Formatted notifications sent to Discord/Mastodon with stock logos and structured data

### Dependencies
- Uses `uv` for Python package management
- Redis for caching (required - runs via Docker)
- WebSocket connection via `httpx-ws`
- Rich Discord embeds and Mastodon API integration
- Regex-based text parsing for financial data extraction

### Testing
- Tests use pytest with async support
- Coverage reporting configured in pyproject.toml
- Includes test files for analyst parsing, news processing, and utilities
- Uses fakeredis for Redis mocking in tests

### Configuration
- Environment-based settings via Pydantic
- Requires .env file with API keys and webhook URLs
- S&P 1500 stock list stored in sp1500_stocks.json
- Sentry integration for error monitoring