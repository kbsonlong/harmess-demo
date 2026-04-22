#!/bin/bash

# 1. 拉起 Kind 集群
echo "=== 1. 拉起 Kind 集群 ==="
uv run python kind_demo.py up --name demo04

# 2. 构建并加载巡检镜像
echo "=== 2. 构建并加载巡检镜像 ==="
docker buildx build --platform linux/arm64 --provenance=false --sbom=false -f Dockerfile.sandbox-inspector -t demo04/sandbox-inspector:local --load .
kind load docker-image demo04/sandbox-inspector:local --name demo04

# 3. 创建沙箱 Pod
echo "=== 3. 创建沙箱 Pod ==="
kubectl apply -f k8s-sandbox.yaml

# 4. 等待 Pod 就绪
echo "=== 4. 等待 Pod 就绪 ==="
kubectl wait --for=condition=Ready pod -l app=sandbox-inspector --timeout=120s

# 5. 在沙箱内执行巡检
echo "=== 5. 执行巡检 ==="
MAX_FINDINGS=50
SANDBOX_POD=$(kubectl get pod -l app=sandbox-inspector -o jsonpath='{.items[0].metadata.name}')
echo "沙箱 Pod: $SANDBOX_POD"

# 执行巡检并保存结果
kubectl exec -n default $SANDBOX_POD -- python -m sandbox_inspector.cli run --max-findings $MAX_FINDINGS > /Users/zengshenglong/Code/PyWorkSpace/test/demo04/reports/inspection_results.json

echo "=== 巡检完成，结果已保存到 /Users/zengshenglong/Code/PyWorkSpace/test/demo04/reports/inspection_results.json ==="

# 6. 清理（可选）
# echo "=== 清理 ==="
# kubectl delete -f k8s-sandbox.yaml
# uv run python kind_demo.py down --name demo04
