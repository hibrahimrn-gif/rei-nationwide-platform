#!/usr/bin/env python3
"""
REI Nationwide - Slack Bot
==========================
Slash commands for team to interact with REI tools directly in Slack.

Commands:
/rei lookup [address] - Property lookup
/rei search [city, state] - Search high equity leads
/rei buyers [city, state] - Find cash buyers
/rei skip [address] - Skip trace (managers only)
/rei ask [question] - Ask AI assistant
/rei help - Show available commands
"""

import os
import re
import json
import hmac
import hashlib
import time
from typing import Optional
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
import httpx

# ============================================================================
# CONFIGURATION
# ============================================================================

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET")
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
BOT_API_TOKEN = os.getenv("BOT_API_TOKEN", "")  # Service account token

# Initialize Slack app
app = App(token=SLACK_BOT_TOKEN, signing_secret=SLACK_SIGNING_SECRET)

# ============================================================================
# API CLIENT
# ============================================================================

class APIClient:
    """Client to interact with REI API"""
    
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
    
    async def post(self, endpoint: str, data: dict) -> dict:
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                resp = await client.post(
                    f"{self.base_url}{endpoint}",
                    json=data,
                    headers=self.headers
                )
                return resp.json()
            except Exception as e:
                return {"error": str(e)}
    
    def post_sync(self, endpoint: str, data: dict) -> dict:
        """Synchronous POST for Slack handlers"""
        try:
            resp = httpx.post(
                f"{self.base_url}{endpoint}",
                json=data,
                headers=self.headers,
                timeout=30.0
            )
            return resp.json()
        except Exception as e:
            return {"error": str(e)}

api = APIClient(API_BASE_URL, BOT_API_TOKEN)

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def format_property(prop: dict) -> str:
    """Format property data for Slack"""
    addr = prop.get("address", {})
    if isinstance(addr, dict):
        street = addr.get("street", "Unknown")
        city = addr.get("city", "")
        state = addr.get("state", "")
        address_str = f"{street}, {city}, {state}"
    else:
        address_str = str(addr)
    
    equity = prop.get("equity_percent", "N/A")
    value = prop.get("estimated_value", 0)
    year = prop.get("year_built", "N/A")
    
    return f"📍 *{address_str}*\n   Equity: {equity}% | Value: ${value:,} | Built: {year}"

def format_buyer(buyer: dict) -> str:
    """Format buyer data for Slack"""
    name = buyer.get("name", "Unknown")
    count = buyer.get("purchase_count", 0)
    return f"💰 *{name}* - {count} purchases in last 12 months"

def parse_location(text: str) -> tuple:
    """Parse city, state from text like 'Plano, TX' or 'Plano TX'"""
    # Remove extra whitespace
    text = " ".join(text.split())
    
    # Try comma-separated
    if "," in text:
        parts = [p.strip() for p in text.split(",")]
        if len(parts) >= 2:
            return parts[0], parts[1]
    
    # Try space-separated (last word is state)
    parts = text.split()
    if len(parts) >= 2:
        state = parts[-1].upper()
        city = " ".join(parts[:-1])
        return city, state
    
    return text, "TX"  # Default to TX

# ============================================================================
# SLASH COMMANDS
# ============================================================================

@app.command("/rei")
def handle_rei_command(ack, command, respond):
    """Main /rei command handler"""
    ack()  # Acknowledge immediately
    
    text = command.get("text", "").strip()
    user_id = command.get("user_id")
    user_name = command.get("user_name")
    
    if not text:
        respond(get_help_message())
        return
    
    # Parse subcommand
    parts = text.split(maxsplit=1)
    subcommand = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""
    
    # Route to appropriate handler
    handlers = {
        "help": lambda: respond(get_help_message()),
        "lookup": lambda: handle_lookup(args, respond, user_name),
        "search": lambda: handle_search(args, respond, user_name),
        "buyers": lambda: handle_buyers(args, respond, user_name),
        "skip": lambda: handle_skip_trace(args, respond, user_name),
        "ask": lambda: handle_ai_query(args, respond, user_name),
    }
    
    handler = handlers.get(subcommand)
    if handler:
        handler()
    else:
        respond(f"❓ Unknown command: `{subcommand}`\n\nUse `/rei help` to see available commands.")

def get_help_message() -> dict:
    """Return help message with all commands"""
    return {
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "🏠 REI Nationwide Commands"}
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Property Research*\n"
                           "• `/rei lookup [address]` - Get property details\n"
                           "• `/rei search [city, state]` - Find high equity leads\n"
                           "• `/rei buyers [city, state]` - Find cash buyers\n\n"
                           "*Skip Tracing*\n"
                           "• `/rei skip [address]` - Get owner contact info (managers+)\n\n"
                           "*AI Assistant*\n"
                           "• `/rei ask [question]` - Ask the AI anything\n\n"
                           "*Examples:*\n"
                           "```/rei lookup 123 Main St, Plano, TX\n"
                           "/rei search Wylie, TX\n"
                           "/rei buyers Dallas, TX\n"
                           "/rei ask What's a good offer on a 60% equity property?```"
                }
            }
        ]
    }

def handle_lookup(address: str, respond, user_name: str):
    """Handle property lookup"""
    if not address:
        respond("❌ Please provide an address. Example: `/rei lookup 123 Main St, Plano, TX`")
        return
    
    respond(f"🔍 Looking up *{address}*...")
    
    result = api.post_sync("/api/v1/properties/lookup", {"address": address})
    
    if "error" in result:
        respond(f"❌ Error: {result['error']}")
        return
    
    # Format response
    detail = result.get("detail", {}).get("data", {})
    if not detail:
        respond(f"❌ No property found for: {address}")
        return
    
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"📍 Property Details"}},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Address:* {address}\n"
                       f"*Estimated Value:* ${detail.get('estimated_value', 0):,}\n"
                       f"*Year Built:* {detail.get('year_built', 'N/A')}\n"
                       f"*Bedrooms:* {detail.get('bedrooms', 'N/A')} | *Bathrooms:* {detail.get('bathrooms', 'N/A')}\n"
                       f"*Square Feet:* {detail.get('square_feet', 'N/A'):,}\n"
                       f"*Lot Size:* {detail.get('lot_size', 'N/A')} sqft\n"
                       f"*Equity:* {detail.get('equity_percent', 'N/A')}%\n"
                       f"*Owner:* {detail.get('owner', {}).get('name', 'Unknown')}"
            }
        },
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"Requested by @{user_name}"}]}
    ]
    
    respond({"blocks": blocks})

def handle_search(location: str, respond, user_name: str):
    """Handle property search"""
    if not location:
        respond("❌ Please provide a location. Example: `/rei search Plano, TX`")
        return
    
    city, state = parse_location(location)
    respond(f"🔍 Searching high equity leads in *{city}, {state}*...")
    
    result = api.post_sync("/api/v1/properties/search", {
        "city": city,
        "state": state,
        "min_equity": 40,
        "absentee_only": True,
        "max_results": 5
    })
    
    if "error" in result:
        respond(f"❌ Error: {result['error']}")
        return
    
    properties = result.get("data", [])
    
    if not properties:
        respond(f"📭 No high equity leads found in {city}, {state}")
        return
    
    # Format response
    prop_list = "\n".join([format_property(p) for p in properties[:5]])
    
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"🎯 High Equity Leads - {city}, {state}"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": prop_list}},
        {"type": "context", "elements": [
            {"type": "mrkdwn", "text": f"Found {len(properties)} leads | Requested by @{user_name}"}
        ]}
    ]
    
    respond({"blocks": blocks})

def handle_buyers(location: str, respond, user_name: str):
    """Handle buyer search"""
    if not location:
        respond("❌ Please provide a location. Example: `/rei buyers Dallas, TX`")
        return
    
    city, state = parse_location(location)
    respond(f"🔍 Finding cash buyers in *{city}, {state}*...")
    
    result = api.post_sync("/api/v1/buyers/search", {
        "city": city,
        "state": state,
        "min_purchases": 2,
        "max_results": 10
    })
    
    if "error" in result:
        respond(f"❌ Error: {result['error']}")
        return
    
    buyers = result.get("buyers", [])
    
    if not buyers:
        respond(f"📭 No portfolio buyers found in {city}, {state}")
        return
    
    buyer_list = "\n".join([format_buyer(b) for b in buyers[:5]])
    
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"💰 Cash Buyers - {city}, {state}"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": buyer_list}},
        {"type": "context", "elements": [
            {"type": "mrkdwn", "text": f"Found {len(buyers)} portfolio buyers | Requested by @{user_name}"}
        ]}
    ]
    
    respond({"blocks": blocks})

def handle_skip_trace(address: str, respond, user_name: str):
    """Handle skip trace request"""
    if not address:
        respond("❌ Please provide an address. Example: `/rei skip 123 Main St, Plano, TX`")
        return
    
    respond(f"🔍 Skip tracing owner of *{address}*...")
    
    result = api.post_sync("/api/v1/skip-trace", {"address": address})
    
    if "error" in result:
        if "403" in str(result["error"]) or "permission" in str(result["error"]).lower():
            respond("🚫 Skip trace is restricted to managers and acquisitions team.")
            return
        respond(f"❌ Error: {result['error']}")
        return
    
    data = result.get("data", {})
    phones = data.get("phones", [])
    emails = data.get("emails", [])
    
    phone_list = ", ".join(phones[:3]) if phones else "None found"
    email_list = ", ".join(emails[:3]) if emails else "None found"
    
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"📞 Skip Trace Results"}},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Address:* {address}\n"
                       f"*Owner:* {data.get('name', 'Unknown')}\n"
                       f"*Phones:* {phone_list}\n"
                       f"*Emails:* {email_list}"
            }
        },
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"⚠️ Skip trace credits used | Requested by @{user_name}"}]}
    ]
    
    respond({"blocks": blocks})

def handle_ai_query(question: str, respond, user_name: str):
    """Handle AI assistant query"""
    if not question:
        respond("❌ Please ask a question. Example: `/rei ask What's a good MAO formula?`")
        return
    
    respond(f"🤖 Thinking about: *{question[:50]}...*")
    
    result = api.post_sync("/api/v1/ai/query", {
        "query": question,
        "context": "You are the REI Nationwide AI Assistant helping a real estate investment team. Be concise and practical."
    })
    
    if "error" in result:
        respond(f"❌ Error: {result['error']}")
        return
    
    response = result.get("response", "No response generated")
    
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "🤖 AI Assistant"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Q:* {question}"}},
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text": response}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"Asked by @{user_name}"}]}
    ]
    
    respond({"blocks": blocks})

# ============================================================================
# EVENT HANDLERS
# ============================================================================

@app.event("app_mention")
def handle_mention(event, say):
    """Handle @mentions of the bot"""
    text = event.get("text", "")
    user = event.get("user")
    
    # Remove the bot mention from the text
    clean_text = re.sub(r"<@[A-Z0-9]+>", "", text).strip()
    
    if clean_text:
        # Treat mentions as AI queries
        result = api.post_sync("/api/v1/ai/query", {
            "query": clean_text,
            "context": "You are the REI Nationwide AI Assistant. Be helpful and concise."
        })
        
        response = result.get("response", "Sorry, I couldn't process that request.")
        say(f"<@{user}> {response}")
    else:
        say(f"<@{user}> Hey! Use `/rei help` to see what I can do, or just ask me a question!")

@app.event("message")
def handle_dm(event, say):
    """Handle direct messages to the bot"""
    # Only respond to DMs (not channel messages)
    if event.get("channel_type") == "im":
        text = event.get("text", "")
        if text and not text.startswith("/"):
            result = api.post_sync("/api/v1/ai/query", {
                "query": text,
                "context": "You are the REI Nationwide AI Assistant. Be helpful and concise."
            })
            say(result.get("response", "Sorry, I couldn't process that."))

# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    print("🚀 Starting REI Nationwide Slack Bot...")
    
    if SLACK_APP_TOKEN:
        # Socket Mode (recommended for development)
        handler = SocketModeHandler(app, SLACK_APP_TOKEN)
        handler.start()
    else:
        # HTTP Mode (for production with webhooks)
        from slack_bolt.adapter.flask import SlackRequestHandler
        from flask import Flask, request
        
        flask_app = Flask(__name__)
        handler = SlackRequestHandler(app)
        
        @flask_app.route("/slack/events", methods=["POST"])
        def slack_events():
            return handler.handle(request)
        
        flask_app.run(host="0.0.0.0", port=int(os.getenv("PORT", 3000)))
