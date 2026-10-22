# codex on a person's own ChatGPT subscription

A codex agent can run on a ChatGPT subscription instead of an OpenAI API key. Each person connects their own subscription once in the Dev Portal, and every codex turn of the Sessions they start runs on it. An installation places no login for anyone. This page says what a person does, what an installation enables, and why there is no shared login.

## What a person does

1. In the Dev Portal, open the sign-in dialog (where GitHub is connected) and choose *Connect* on the ChatGPT row.
2. The dialog shows a one-time code and OpenAI's link, `https://auth.openai.com/codex/device`. Open the link, sign in to ChatGPT and enter the code. The row turns *Connected*.
3. Pick the model "ChatGPT (your subscription)" for a codex agent, or use an agent that has it. Every codex turn of a Session you start, in the portal or in Slack, runs on your subscription.

Any ChatGPT plan that includes codex works, personal or a company workspace. If OpenAI answers that device-code sign-in is not enabled for the account, enable it in ChatGPT's security settings and connect again. *Disconnect* on the same row drops the login; no later turn uses it.

In a Slack thread someone else started, every turn runs on the subscription of the thread's initiator, as every other credential of that Session does. A person who has not connected gets "Connect your ChatGPT subscription" in the portal's chat instead of a turn; elsewhere (Slack, an API caller) the turn's requests are refused at the egress gateway with "credential provider muster.giantswarm.io denied: the person holds no grant for the provider", and nothing runs on anybody else's account.

## What an installation enables

One `kagent.modelConfigs[]` entry: `credentialType: chatgptOAuth` and **no** `apiKeySecret`.

```yaml
kagent:
  modelConfigs:
    - name: chatgpt-subscription
      displayName: ChatGPT (your subscription)
      provider: OpenAI
      model: gpt-5.6-luna
      credentialType: chatgptOAuth
      apiFormat: responses
```

From that entry the chart derives:

- muster's provider App `chatgpt` (OpenAI's device-code sign-in). muster keeps each person's login as their grant in its encrypted token store and is its only refresher;
- kagent's installation provider `chatgpt` (host `chatgpt.com`, path prefix `/backend-api/codex/`, scheme Bearer), admitted on every Session bound to a person. On a Session whose agent does not run codex on this model it opens nothing: only the codex compile opens egress to `chatgpt.com`;
- the ModelConfig, with no Secret: the codex compiler sends placeholders, and Substrate's egress gateway replaces them on the ChatGPT backend's path with the access token muster releases for the Session's person. The refresh token never leaves muster, and the sandbox never sees a token.

The render fails, naming the key, unless the installation has:

- workspaces with Substrate (`workspaces.enabled`, `components.substrate.enabled`): muster's credential provider and the Session binding that carry the person;
- muster's token encryption (`muster.muster.oauth.server.encryptionKey`, the key `oauth-encryption-key` in muster's OAuth Secret): a person's ChatGPT tokens are never stored unencrypted;
- muster's OAuth client role (`muster.muster.oauth.mcpClient.enabled` with its `publicUrl`);
- kagent and kagent-crds from 1.24.0 and muster from 5.52.5 in `components.*.versionRange` (the chart's defaults).

`chatgptOAuth` also requires `apiFormat: responses` and the `OpenAI` provider, and refuses `apiKeySecretKey`. `model` is one the ChatGPT Codex backend serves to a subscription (`gpt-5.6-luna` is proven); any other is answered with HTTP 400 and the turn fails with "Codex execution failed".

## Why there is no shared login

- **One account serving everyone is not allowed.** OpenAI's terms make the account holder answerable for all activity on the account and forbid making it available to anyone else. A login placed for an installation would put every user's turns on one person's account and limits.
- **The refresh token rotates on every use.** Two refreshers of one login invalidate each other, so a login cannot be copied between installations or shared with a laptop. muster refreshes each person's login under a cross-replica lock, as it does for their GitHub grant.
- **Nothing to place by hand.** An installation needs no device login, no Secret and no person to renew it; codex works for everyone who connects, on every installation alike.

## Not through the LLM listener

Subscription turns stay off the LLM listener (`llmRouting`): the chart writes no listener `baseUrl` into a `chatgptOAuth` ModelConfig, the admission policy skips it, and the reconcile hook takes a listener URL it wrote earlier off it again.

- The listener's OpenAI route serves the OpenAI API (`api.openai.com`), not the ChatGPT Codex backend, which would reject the subscription's token.
- A subscription is billed flat, not per token: the gateway's price catalog has nothing to attribute, and the subscription's own limits bound it instead of `llmRouting.rateLimits`. A turn refused for the plan's limit shows the backend's message.

Subscription turns therefore do not appear in the listener's GenAI usage metrics or the LLM usage board; the platform meters nothing.
