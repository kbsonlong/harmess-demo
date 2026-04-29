# kube-apiserver TLS 证书修复（让 Pod 内 ca.crt 可校验）

本文档用于修复如下问题：

- Pod 内访问 apiserver（Python SDK/urllib3）报 `CERTIFICATE_VERIFY_FAILED`
- `openssl verify -CAfile /var/run/secrets/kubernetes.io/serviceaccount/ca.crt https://<apiserver>` 返回 `Verify return code: 18 (self-signed certificate)`
- 现象根因：kube-apiserver 当前对外服务端证书是自签证书（`subject == issuer`），且不由集群 CA 签发，导致注入到 Pod 的 `serviceaccount/ca.crt` 无法信任它。

目标：

- 让 kube-apiserver 的服务端证书由集群 CA（示例：`/data/k8s/ssl/ca.pem`）签发
- 最终在任意 Pod 内使用 `/var/run/secrets/kubernetes.io/serviceaccount/ca.crt` 可通过严格 TLS 校验访问 apiserver

## 0. 风险提示与回滚策略

- 涉及控制面证书替换与重启 kube-apiserver，会影响集群 API 可用性（短暂中断或连接重建）。
- 强烈建议在维护窗口执行，并提前准备回滚。
- 所有操作建议在控制面节点上以 root 执行，且妥善保护 CA 私钥（`ca-key.pem`）。

回滚点：

- 保留旧证书与旧私钥备份（本流程会自动备份）
- 如果替换后 `kubectl`/apiserver 不可用，立即恢复旧文件并重启 apiserver

## 1. 确认现状（可选但推荐）

### 1.1 确认 Pod 内注入的 ca.crt 与控制面 CA 一致

在 Pod 内：

```bash
md5sum /var/run/secrets/kubernetes.io/serviceaccount/ca.crt
```

在控制面节点上：

```bash
md5sum /data/k8s/ssl/ca.pem
```

两者一致表示 Pod 内 ca.crt 对应的就是控制面配置的 CA bundle。

### 1.2 确认 kube-apiserver 对外证书是自签

在控制面节点上：

```bash
openssl x509 -in /data/k8s/ssl/kubernetes.pem -noout -subject -issuer -serial -fingerprint -sha256
openssl verify -CAfile /data/k8s/ssl/ca.pem /data/k8s/ssl/kubernetes.pem
```

如果出现 `error 18 ... self-signed certificate`，说明 `kubernetes.pem` 不是由 `ca.pem` 签发。

## 2. 规划 SAN（必须正确）

新证书必须包含正确的 Subject Alternative Names（SAN），否则会出现 “x509: certificate is valid for ... not ...” 的主机名/IP 不匹配问题。

建议至少包含：

- DNS：
  - `kubernetes`
  - `kubernetes.default`
  - `kubernetes.default.svc`
  - `kubernetes.default.svc.<clusterDomain>`（常见为 `cluster.local`）
- IP：
  - `kubernetes` Service 的 ClusterIP（示例：`10.254.0.1`）
  - apiserver `--advertise-address`（示例：`10.98.32.30`）
  - 若存在 VIP/LB，请将 VIP/LB IP 也加入

查看当前证书的 SAN（用于对比缺失项）：

```bash
openssl x509 -in /data/k8s/ssl/kubernetes.pem -noout -text | sed -n '/Subject Alternative Name/,+2p'
```

## 3. 生成 CSR 并由 CA 签发新证书

以下示例基于你的目录结构：

- CA：`/data/k8s/ssl/ca.pem`
- CA 私钥：`/data/k8s/ssl/ca-key.pem`
- apiserver 证书：`/data/k8s/ssl/kubernetes.pem`
- apiserver 私钥：`/data/k8s/ssl/kubernetes-key.pem`

### 3.1 生成 openssl 配置（按需改 SAN）

```bash
cat >/tmp/apiserver-openssl.cnf <<'EOF'
[ req ]
default_bits       = 2048
prompt             = no
default_md         = sha256
distinguished_name = dn
req_extensions     = req_ext

[ dn ]
C  = China
ST = Beijing
L  = Beijing
O  = Kubernetes
OU = Kubernetes
CN = kubernetes

[ req_ext ]
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = @alt_names

[ alt_names ]
DNS.1 = kubernetes
DNS.2 = kubernetes.default
DNS.3 = kubernetes.default.svc
DNS.4 = kubernetes.default.svc.cluster.local
IP.1  = 10.254.0.1
IP.2  = 10.98.32.30
EOF
```

### 3.2 生成新私钥与 CSR

```bash
openssl genrsa -out /data/k8s/ssl/kubernetes-key.new.pem 2048
openssl req -new \
  -key /data/k8s/ssl/kubernetes-key.new.pem \
  -out /tmp/kubernetes.csr \
  -config /tmp/apiserver-openssl.cnf
```

### 3.3 用 CA 签发新证书

```bash
openssl x509 -req \
  -in /tmp/kubernetes.csr \
  -CA /data/k8s/ssl/ca.pem \
  -CAkey /data/k8s/ssl/ca-key.pem \
  -CAcreateserial \
  -out /data/k8s/ssl/kubernetes.new.pem \
  -days 3650 \
  -sha256 \
  -extensions req_ext \
  -extfile /tmp/apiserver-openssl.cnf
```

## 4. 验证新证书

```bash
openssl verify -CAfile /data/k8s/ssl/ca.pem /data/k8s/ssl/kubernetes.new.pem
openssl x509 -in /data/k8s/ssl/kubernetes.new.pem -noout -issuer -subject -serial -fingerprint -sha256
openssl x509 -in /data/k8s/ssl/kubernetes.new.pem -noout -text | sed -n '/Subject Alternative Name/,+2p'
```

期望：

- `openssl verify` 输出 `OK`
- `issuer` 不再等于 `subject`

## 5. 原子替换与重启 kube-apiserver

### 5.1 备份旧文件（回滚点）

```bash
TS="$(date +%F-%H%M%S)"
cp /data/k8s/ssl/kubernetes.pem /data/k8s/ssl/kubernetes.pem.bak.$TS
cp /data/k8s/ssl/kubernetes-key.pem /data/k8s/ssl/kubernetes-key.pem.bak.$TS
```

### 5.2 替换证书与私钥

```bash
mv /data/k8s/ssl/kubernetes.new.pem /data/k8s/ssl/kubernetes.pem
mv /data/k8s/ssl/kubernetes-key.new.pem /data/k8s/ssl/kubernetes-key.pem
chmod 600 /data/k8s/ssl/kubernetes-key.pem
```

### 5.3 重启 apiserver

根据你的部署方式选择其一：

- systemd：
  - `systemctl restart kube-apiserver`
- supervisor：
  - `supervisorctl restart kube-apiserver`
- 手工进程：
  - kill 并重新拉起（确保参数不变）

## 6. 在 Pod 内验证修复效果

在任意 Pod 内执行（示例：k8s-sandbox）：

```bash
openssl s_client -connect 10.254.0.1:443 \
  -CAfile /var/run/secrets/kubernetes.io/serviceaccount/ca.crt \
  -verify_return_error </dev/null 2>/dev/null | tail -n 5
```

期望：

- `Verify return code: 0 (ok)`

再验证 Python SDK（不跳过 TLS）：

```bash
unset SANDBOX_INSECURE_SKIP_TLS_VERIFY
unset SANDBOX_SSL_CA_CERT
python -m sandbox_inspector.cli run --max-findings 50
```

## 7. 回滚流程

若替换后出现不可用：

```bash
TS="<替换时的时间戳>"
cp /data/k8s/ssl/kubernetes.pem.bak.$TS /data/k8s/ssl/kubernetes.pem
cp /data/k8s/ssl/kubernetes-key.pem.bak.$TS /data/k8s/ssl/kubernetes-key.pem
chmod 600 /data/k8s/ssl/kubernetes-key.pem
```

然后重启 kube-apiserver 并复测。

