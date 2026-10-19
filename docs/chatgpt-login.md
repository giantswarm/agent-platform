# A ChatGPT subscription login as a codex model

A codex agent can run on a ChatGPT subscription instead of an OpenAI API key. The model is a `kagent.modelConfigs[]` entry with `credentialType: chatgptOAuth`; its `apiKeySecret` names a Secret that holds one installation's own ChatGPT login. This page says how the entry is written, how an installation's login is created and placed, and why its turns do not go through the LLM listener.

## The entry

```yaml
kagent:
  modelConfigs:
    - name: openai-chatgpt-codex
      displayName: GPT-5.6 Luna (ChatGPT)
      provider: OpenAI
      model: gpt-5.6-luna
      apiKeySecret: kagent-chatgpt-login   # this installation's login
      credentialType: chatgptOAuth
      apiFormat: responses
```

The chart renders `spec.openAI.credentialType: chatgptOAuth` and no `apiKeySecretKey`: the login's Secret holds the fixed keys `access_token`, `refresh_token` and `account_id`. The render fails, naming the entry, on:

- `credentialType` on any provider but `OpenAI`, or outside the CRD's values `apiKey`, `chatgptOAuth`;
- `apiKeySecretKey` beside `chatgptOAuth` (the CRD refuses it at admission);
- `chatgptOAuth` without `apiFormat: responses` (a ChatGPT login serves codex only, and the codex compiler requires the Responses API).

`model` is one the ChatGPT Codex backend serves to a subscription (`gpt-5.6-luna` is proven); the backend answers any other with HTTP 400 and the turn fails with "Codex execution failed". It needs the kagent line from 1.16.0, which the chart's kagent range already selects. The codex compiler sends the turns to `https://chatgpt.com/backend-api/codex` and opens the agent's egress to it; the Substrate egress gateway adds the access token and the account id on that path only.

## One login per installation

The refresh token rotates on every use, and the kagent controller is the login's only refresher: it renews the access token before it expires and writes the new access and refresh tokens back into the same Secret. Two refreshers on one login invalidate each other, so a login is never copied between installations or from a laptop that keeps using it. Each installation gets a login of its own:

1. On a workstation, log in with a fresh, empty Codex home, which nothing else uses afterwards:

   ```sh
   export CODEX_HOME="$(mktemp -d)"
   codex login --device-auth
   ```

   The device-auth flow prints a URL and a one-time code; a person with the ChatGPT subscription opens the URL, signs in and enters the code. Codex writes `$CODEX_HOME/auth.json`.

2. Build the Secret in the kagent namespace from `auth.json` (`.tokens.access_token`, `.tokens.refresh_token`, `.tokens.account_id`), labelled for the refresher:

   ```yaml
   apiVersion: v1
   kind: Secret
   metadata:
     name: kagent-chatgpt-login
     namespace: kagent
     labels:
       kagent.dev/chatgpt-login: "true"
     annotations:
       # Flux creates the Secret once and never re-applies it: the controller
       # rotates the tokens in it, and a re-apply would put back a spent
       # refresh token.
       kustomize.toolkit.fluxcd.io/ssa: IfNotPresent
   type: Opaque
   stringData:
     access_token: <tokens.access_token>
     refresh_token: <tokens.refresh_token>
     account_id: <tokens.account_id>
   ```

3. Encrypt it with the installation's SOPS key, commit it to the installation's configuration, and delete the Codex home: from here on the Secret in the cluster is the only copy that stays valid.

The controller rewrites only a Secret with the label `kagent.dev/chatgpt-login: "true"`; a ModelConfig naming any other Secret reports `ResolvedRefs` false with `ChatGPTLoginNotOptedIn`. A login that has lapsed (the subscription ended, the refresh token was spent elsewhere) is replaced the same way: a new device-auth login, and the Secret recreated from it (delete the in-cluster Secret so Flux creates it again).

## Not through the LLM listener

A ChatGPT login stays off the LLM listener (`llmRouting`): the chart writes no listener `baseUrl` into its ModelConfig, the admission policy skips a ModelConfig with `credentialType: chatgptOAuth`, and the reconcile hook takes a listener URL it wrote earlier off such a ModelConfig again.

- The listener's OpenAI route serves the OpenAI API (`/v1/responses` upstream at `api.openai.com`), not the ChatGPT Codex backend. kagent sends the login to whatever `baseUrl` the ModelConfig carries, so a listener URL would send the subscription's token to the OpenAI API, which rejects it.
- A subscription is billed flat, not per token: the gateway's price catalog has nothing to attribute, and the subscription's own usage limits bound it instead of `llmRouting.rateLimits`.
- The token is added by Substrate's egress gateway on the ChatGPT backend's path only; a route through the listener would need the listener to hold or forward the login as well.

Subscription turns therefore do not appear in the listener's GenAI usage metrics or the LLM usage board. A ChatGPT route on the listener is a separate change, should per-agent usage of subscription turns be wanted.
