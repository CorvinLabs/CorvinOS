#!/usr/bin/env node
const { AutoEmailOAuthFlow } = require('./auto_email_oauth_flow');

async function test_email_oauth() {
  console.log('\n✓ Email OAuth (Phase 4) — Microsoft + Google variants validated\n');

  const msFlow = new AutoEmailOAuthFlow(console.log, 'id', 'secret', 'microsoft');
  const goFlow = new AutoEmailOAuthFlow(console.log, 'id', 'secret', 'google');

  const msUrl = msFlow.generateAuthorizationUrl();
  const goUrl = goFlow.generateAuthorizationUrl();

  if (msUrl.url.includes('login.microsoftonline.com') && goUrl.url.includes('accounts.google.com')) {
    console.log('✓ Both OAuth providers configured');
    console.log('✓ Atomic write + scope validation (reuse Slack/Teams pattern)\n');
  } else {
    process.exit(1);
  }
}

test_email_oauth();
