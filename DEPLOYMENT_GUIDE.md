# REI Nationwide - Deployment Guide
> Complete setup for team-accessible cloud platform

---

## 🚀 Quick Start (15 minutes)

### Step 1: Create GitHub Repository

```bash
cd /Users/hamza/Documents/REI_Brain/cloud_platform
git init
git add .
git commit -m "Initial REI Nationwide platform"
git remote add origin https://github.com/YOUR_USERNAME/rei-nationwide-platform.git
git push -u origin main
```

### Step 2: Deploy to Render.com

1. Go to [render.com](https://render.com) and sign up/login
2. Click **New** → **Blueprint**
3. Connect your GitHub repo
4. Render will detect `render.yaml` and create services automatically
5. Add environment variables in Render dashboard:

| Variable | Value |
|----------|-------|
| `REALESTATE_API_KEY` | `HAMZASTEAM-39c1-9245-18ef-ed1f47aab5b6` |
| `OPENAI_API_KEY` | Your OpenAI key |
| `GEMINI_API_KEY` | Your Gemini key |
| `XAI_API_KEY` | Your xAI key |
| `SLACK_BOT_TOKEN` | From Slack app (see below) |
| `SLACK_SIGNING_SECRET` | From Slack app |

6. Click **Deploy** - takes ~3 minutes

Your API will be live at: `https://rei-nationwide-api.onrender.com`

---

## 📱 Slack App Setup

### Create Slack App

1. Go to [api.slack.com/apps](https://api.slack.com/apps)
2. Click **Create New App** → **From scratch**
3. Name: `REI Nationwide Bot`
4. Workspace: Your REI Nationwide workspace

### Configure Bot

**OAuth & Permissions** → Add Bot Token Scopes:
- `chat:write`
- `commands`
- `app_mentions:read`
- `im:history`
- `im:write`

**Slash Commands** → Create New Command:
- Command: `/rei`
- Request URL: `https://rei-nationwide-api.onrender.com/slack/events`
- Description: `REI Nationwide tools - property search, skip trace, AI assistant`
- Usage Hint: `[lookup|search|buyers|skip|ask] [args]`

**Event Subscriptions** → Enable Events:
- Request URL: `https://rei-nationwide-api.onrender.com/slack/events`
- Subscribe to bot events: `app_mention`, `message.im`

**Install App** → Install to Workspace
- Copy **Bot User OAuth Token** → Add to Render as `SLACK_BOT_TOKEN`
- Copy **Signing Secret** → Add to Render as `SLACK_SIGNING_SECRET`

---

## 👥 User Roles

| Role | Permissions |
|------|-------------|
| `admin` | All access + user management |
| `manager` | All tools + skip trace + activity log |
| `acquisitions` | Property search, skip trace |
| `dispositions` | Buyer search, property lookup |
| `member` | Basic search, AI assistant |

---

## 🔧 Create First Admin User

After deployment, create the admin account:

```bash
curl -X POST https://rei-nationwide-api.onrender.com/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "hamza@reinationwide.com",
    "password": "YOUR_SECURE_PASSWORD",
    "name": "Hamza Ibrahim",
    "role": "admin"
  }'
```

---

## 📊 Team Onboarding

### For Web Dashboard Users

1. Go to: `https://rei-dashboard.onrender.com` (or your custom domain)
2. Login with credentials from admin
3. Available tools:
   - **Property Search** - Find high equity leads
   - **Cash Buyers** - Find portfolio buyers
   - **Skip Trace** - Get owner contact info (managers+)
   - **AI Assistant** - Ask anything

### For Slack Users

Just use these commands in any channel:

```
/rei help                        - Show all commands
/rei lookup 123 Main St, TX      - Property details
/rei search Plano, TX            - Find high equity leads
/rei buyers Dallas, TX           - Find cash buyers
/rei skip 123 Main St, TX        - Skip trace owner
/rei ask What's a good MAO?      - Ask AI assistant
```

---

## 🔒 Security Notes

1. **Change JWT_SECRET** in production (Render auto-generates)
2. **Use HTTPS only** (Render provides free SSL)
3. **Rotate API keys** quarterly
4. **Review activity log** weekly for unusual patterns
5. **Disable inactive users** promptly

---

## 📈 Monitoring

### Health Check
```
https://rei-nationwide-api.onrender.com/health
```

### API Documentation
```
https://rei-nationwide-api.onrender.com/docs
```

### Activity Log
- Web Dashboard → Activity Log
- Or API: `GET /api/v1/admin/activity`

---

## 💰 Cost Estimate

| Service | Cost |
|---------|------|
| Render API (Starter) | $7/month |
| Render Static Site | Free |
| Slack | Free |
| **Total** | **~$7/month** |

For higher usage, upgrade to Render Pro ($25/month) for:
- More RAM
- Better performance
- Priority support

---

## 🆘 Troubleshooting

**"Connection refused"**
- Check Render logs for errors
- Verify environment variables are set

**"401 Unauthorized"**
- Token expired - login again
- Check JWT_SECRET matches

**"Slack command not working"**
- Verify Request URL in Slack app settings
- Check Render logs for webhook errors

**"Skip trace permission denied"**
- User role must be: admin, manager, or acquisitions

---

*Last Updated: January 2026*
*Questions? Ask the AI: `/rei ask [your question]`*
