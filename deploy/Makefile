IMAGE ?= ghcr.io/example/mithai
TAG   ?= latest
FULL_IMAGE = $(IMAGE):$(TAG)

NS = mithai

# Build context is the repo root so Dockerfile can COPY src/ and skills/
BUILD_CONTEXT = ..

.PHONY: build push deploy delete logs status restart shell

## Create the registry pull secret (run once after docker login)
## Usage: make create-pull-secret REGISTRY_USER=<user> REGISTRY_PASS=<pass>
create-pull-secret:
	kubectl -n $(NS) create secret docker-registry private-registry \
		--docker-server=container-registry.example \
		--docker-username=$(REGISTRY_USER) \
		--docker-password=$(REGISTRY_PASS) \
		--dry-run=client -o yaml | kubectl apply -f -

## Build the Docker image
build:
	docker build --platform linux/amd64 -f Dockerfile -t $(FULL_IMAGE) $(BUILD_CONTEXT)

## Push the image to the registry
push:
	docker push $(FULL_IMAGE)

## Build and push in one step
ship: build push

## Apply all K8s manifests (idempotent)
deploy:
	kubectl apply --validate=false -f k8s/namespace.yaml
	kubectl apply --validate=false -f k8s/rbac.yaml
	kubectl apply --validate=false -f k8s/secret.yaml
	kubectl apply --validate=false -f k8s/configmap.yaml
	kubectl apply --validate=false -f k8s/deployment.yaml
	@echo ""
	@echo "Waiting for rollout..."
	kubectl -n $(NS) rollout status deployment/mithai

## Rebuild image, push, and trigger a rolling restart
redeploy: ship
	kubectl -n $(NS) rollout restart deployment/mithai
	kubectl -n $(NS) rollout status deployment/mithai

## Tear down the deployment (keeps namespace and secrets)
delete:
	kubectl delete -f k8s/deployment.yaml --ignore-not-found
	kubectl delete -f k8s/configmap.yaml --ignore-not-found

## Tail logs from the running pod
logs:
	kubectl -n $(NS) logs -f deployment/mithai

## Show pod and deployment status
status:
	@echo "=== Deployment ==="
	kubectl -n $(NS) get deployment mithai
	@echo ""
	@echo "=== Pods ==="
	kubectl -n $(NS) get pods -l app.kubernetes.io/name=mithai
	@echo ""
	@echo "=== Recent Events ==="
	kubectl -n $(NS) get events --sort-by=.lastTimestamp | tail -10

## Rolling restart without rebuilding
restart:
	kubectl -n $(NS) rollout restart deployment/mithai
	kubectl -n $(NS) rollout status deployment/mithai

## Exec a shell into the running pod
shell:
	kubectl -n $(NS) exec -it deployment/mithai -- /bin/bash
