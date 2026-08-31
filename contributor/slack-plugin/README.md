# Slack Notifier Plugin

Send notifications and messages to Slack channels from CorvinOS. Real-time alerts, scheduled reminders, and rich formatting support.

## Features

- **Send Messages**: Post to channels, threads, and DMs
- **Scheduled Reminders**: Set up recurring alerts and notifications
- **Rich Formatting**: Bold, italic, lists, code blocks, images
- **Thread Support**: Reply to messages in threads
- **OAuth Authentication**: Secure token-based authentication
- **Error Handling**: Graceful degradation with fallback notifications

## Installation

1. Open CorvinOS Console
2. Navigate to **Marketplace → Extensions**
3. Search for "Slack Notifier"
4. Click **Install**
5. Configure your Slack workspace URL and OAuth token

## Configuration

Set these environment variables or via Console Settings:

```
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/WEBHOOK/URL
SLACK_BOT_TOKEN=xoxb-your-bot-token
SLACK_APP_ID=A1234567890
```

## Usage

### Send a Message

```python
from corvin_plugins import slack_notifier

slack_notifier.send_message(
    channel="#alerts",
    text="Database backup completed",
    rich=True
)
```

### Schedule a Reminder

```python
slack_notifier.schedule_reminder(
    channel="#team",
    message="Daily standup in 5 minutes",
    interval="daily",
    time="09:55"
)
```

## Security

- Tokens are encrypted at rest (AES-256-GCM)
- No tokens logged or transmitted to telemetry
- Per-channel permission scoping
- Audit trail for all messages sent

## Support

- Issues: https://github.com/corvin-community/slack-notifier/issues
- Discussions: https://github.com/corvin-community/slack-notifier/discussions
- Docs: https://docs.corvin.org/plugins/slack-notifier

## License

Apache License 2.0 — See LICENSE file for details.
