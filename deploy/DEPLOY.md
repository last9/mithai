# Deploying mithai to a Kubernetes Cluster

## Architecture

```
                          ┌─────────────────────────────────────────────────────┐
                          │               Kubernetes Cluster                     │
                          │                                                      │
                          │   namespace: mithai                                  │
                          │   ┌──────────────────────────────────────────────┐  │
                          │   │  Deployment: mithai (1 replica)              │  │
                          │   │  ┌────────────────────────────────────────┐  │  │
  ┌──────────┐  messages  │   │  │  Pod                                   │  │  │
  │  Slack   │◄──────────►│   │  │  ┌──────────────────────────────────┐  │  │  │
  └──────────┘            │   │  │  │  mithai container                │  │  │  │
                          │   │  │  │                                  │  │  │  │
  ┌──────────┐  API calls │   │  │  │  ┌────────────┐  ┌───────────┐  │  │  │  │
  │ Anthropic│◄──────────►│   │  │  │  │   Engine   │  │  Skills   │  │  │  │  │
  │  Claude  │            │   │  │  │  │  (LLM loop)│  │  k8s/aws  │  │  │  │  │
  └──────────┘            │   │  │  │  └─────┬──────┘  └─────┬─────┘  │  │  │  │
                          │   │  │  │        │                │        │  │  │  │
                          │   │  │  │        └────────────────┘        │  │  │  │
                          │   │  │  │                 │                │  │  │  │
                          │   │  │  │         kubectl (in-cluster SA)  │  │  │  │
                          │   │  │  └─────────────────┼────────────────┘  │  │  │
                          │   │  │                    │                   │  │  │
                          │   │  │   Volumes          │                   │  │  │
                          │   │  │  ┌─────────────┐   │                   │  │  │
                          │   │  │  │  ConfigMap  │   │                   │  │  │
                          │   │  │  │ (config.yaml│   │                   │  │  │
                          │   │  │  │  /config)   │   │                   │  │  │
                          │   │  │  └─────────────┘   │                   │  │  │
                          │   │  │  ┌─────────────┐   │                   │  │  │
                          │   │  │  │   Secret    │   │                   │  │  │
                          │   │  │  │ (tokens →   │   │                   │  │  │
                          │   │  │  │  env vars)  │   │                   │  │  │
                          │   │  │  └─────────────┘   │                   │  │  │
                          │   │  │  ┌─────────────┐   │                   │  │  │
                          │   │  │  │  emptyDir   │   │                   │  │  │
                          │   │  │  │ state/memory│   │                   │  │  │
                          │   │  │  └─────────────┘   │                   │  │  │
                          │   │  └────────────────────┼───────────────────┘  │  │
                          │   └───────────────────────┼──────────────────────┘  │
                          │                           │                          │
                          │   ┌───────────────────────▼──────────────────────┐  │
                          │   │  Kubernetes API (ClusterRole bindings)        │  │
                          │   │  mithai-reader  → get/list/watch all resources │  │
                          │   │  mithai-operator → patch/scale/evict (approved)│  │
                          │   └──────────────────────────────────────────────┘  │
                          │                                                      │
                          │   Registry: docker-registry.last9.io                │
                          │   Pull Secret: last9-registry                        │
                          └─────────────────────────────────────────────────────┘
```

**Flow:**
1. Slack message → mithai bot → Engine builds prompt with all skill tools
2. Engine calls Claude API (Anthropic) in a tool-use loop
3. Claude requests a kubectl tool → skill handler runs `kubectl` using the pod's ServiceAccount token
4. Mutating actions (restart, scale, drain) require human approval back in Slack
5. Self-healing loop runs in background — scans cluster every N minutes, posts alerts to Slack, auto-investigates in thread

---

## Prerequisites

| Tool | Version | Purpose |
|---|---|---|
| `docker` | 20+ | Build and push the image |
| `kubectl` | 1.24+ | Apply manifests and manage the deployment |
| `make` | any | Run Makefile targets |
| Registry access | — | Push image to `docker-registry.last9.io` |
| Cluster access | — | `kubectl` pointed at the target cluster |

---

## Step 1 — Set your kubeconfig

Point `kubectl` at the target cluster before running any commands:

```bash
export KUBECONFIG=/path/to/your/kubeconfig
# Verify access
kubectl get nodes
```

---

## Step 2 — Configure the image registry

The default registry is `docker-registry.last9.io/mithai`. To use a different one:

```bash
# Override at build time
make build IMAGE=your.registry/mithai
make push  IMAGE=your.registry/mithai

# Or edit the default in deploy/Makefile
IMAGE ?= your.registry/mithai
```

Also update `deploy/k8s/deployment.yaml` to match:

```yaml
image: your.registry/mithai:latest
```

---

## Step 3 — Build and push the image

Run from the `deploy/` directory:

```bash
cd deploy
make ship        # docker build --platform linux/amd64 + docker push
```

> The build targets `linux/amd64` explicitly. This is required when building on Apple Silicon (M1/M2 Macs) for amd64 clusters.

---

## Step 4 — Create the namespace

```bash
kubectl apply --validate=false -f k8s/namespace.yaml
```

---

## Step 5 — Create the image pull secret

The cluster needs credentials to pull from the private registry:

```bash
make create-pull-secret REGISTRY_USER=<user> REGISTRY_PASS=<password>
```

Or create it directly from your local Docker login session:

```bash
kubectl -n mithai create secret generic last9-registry \
  --from-file=.dockerconfigjson=$HOME/.docker/config.json \
  --type=kubernetes.io/dockerconfigjson \
  --dry-run=client -o yaml | kubectl apply -f -
```

---

## Step 6 — Fill in secrets

Edit `deploy/k8s/secret.yaml` with real values. **Never commit this file.**

```yaml
stringData:
  ANTHROPIC_API_KEY: "sk-ant-..."
  SLACK_BOT_TOKEN: "xoxb-..."
  SLACK_APP_TOKEN: "xapp-..."
```

Apply it:

```bash
kubectl apply --validate=false -f k8s/secret.yaml
```

---

## Step 7 — Customize the config (optional)

`deploy/k8s/configmap.yaml` contains the full `config.yaml` mounted into the pod. Key fields to review before deploying to a new cluster:

```yaml
kubernetes:
  kubeconfig: ""          # empty = in-cluster ServiceAccount token (recommended)
  context: ""             # empty = default context in kubeconfig
  default_namespace: default
  alert_channel: ""       # Slack channel ID for self-healing alerts e.g. C0XXXXXX
  poll_interval_minutes: 5
  cooldown_minutes: 30
  exclude_namespaces:
    - kube-system
    - kube-public
    - kube-node-lease

adapter:
  slack:
    bot_token: ${SLACK_BOT_TOKEN}   # injected from Secret
    app_token: ${SLACK_APP_TOKEN}
```

Apply after edits:

```bash
kubectl apply --validate=false -f k8s/configmap.yaml
```

---

## Step 8 — Apply RBAC

Creates the `mithai` ServiceAccount, `mithai-reader` (read-only) and `mithai-operator` (targeted mutating) ClusterRoles, and their bindings:

```bash
kubectl apply --validate=false -f k8s/rbac.yaml
```

---

## Step 9 — Deploy

```bash
make deploy
```

This applies all manifests in order and waits for the rollout to complete.

Or apply just the deployment if everything else is already in place:

```bash
kubectl apply --validate=false -f k8s/deployment.yaml
kubectl -n mithai rollout status deployment/mithai
```

---

## Step 10 — Verify

```bash
make status          # pod status + recent events
make logs            # tail live logs
```

Expected healthy log output:

```
INFO: Loaded skill: kubernetes (17 tools)
INFO: Loaded skill: memory (3 tools)
...
INFO: Starting Slack adapter
```

---

## Day-to-day Operations

| Command | Description |
|---|---|
| `make logs` | Tail live pod logs |
| `make status` | Show pod status and recent events |
| `make restart` | Rolling restart (no image rebuild) |
| `make redeploy` | Rebuild image, push, rolling restart |
| `make shell` | Exec a bash shell into the running pod |
| `make delete` | Remove the deployment and configmap (keeps namespace + secrets) |

---

## Updating mithai

After code changes:

```bash
cd deploy
make redeploy        # build + push + rolling restart
```

After config-only changes (no code change):

```bash
kubectl apply --validate=false -f k8s/configmap.yaml
make restart         # pod picks up new ConfigMap on restart
```

---

## Troubleshooting

### `exec /app/.venv/bin/mithai: no such file or directory`
Image was built for the wrong architecture. Always build with:
```bash
docker build --platform linux/amd64 ...
```
The Makefile already includes this flag via `make build`.

### `ImagePullBackOff`
The cluster cannot pull the image. Check the pull secret:
```bash
kubectl -n mithai get secret last9-registry
kubectl -n mithai describe pod <pod-name>
```
Recreate the pull secret with correct credentials (Step 5).

### `Unauthorized` on kubectl commands
Your kubeconfig token has expired or `KUBECONFIG` is not set:
```bash
export KUBECONFIG=/path/to/kubeconfig
kubectl get nodes   # verify access
```

### `error validating data: failed to download openapi`
Schema validation requires cluster API access. Use `--validate=false`:
```bash
kubectl apply --validate=false -f k8s/<file>.yaml
```
The Makefile `deploy` target already includes this flag.

### Bot starts but does not respond on Slack
Check that the Slack tokens in `secret.yaml` are correct and the bot is invited to the channel:
```bash
make logs   # look for Slack connection errors
```

### Self-healing loop not posting alerts
The `alert_channel` in `configmap.yaml` must be set to a Slack channel ID (not name):
```yaml
kubernetes:
  alert_channel: "C0XXXXXXXXX"   # channel ID, not #channel-name
```
Apply the updated configmap and restart:
```bash
kubectl apply --validate=false -f k8s/configmap.yaml
make restart
```
