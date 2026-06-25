---
name: deploy-k8s-service
version: 2
description: K8s 서비스 배포 절차 (production 네임스페이스)
state: active
tags: [kubernetes, deploy, production]
source: eval:warm-seed
created_at: 2024-01-01T00:00:00Z
updated_at: 2024-06-01T00:00:00Z
---

## K8s 서비스 배포 절차

production 네임스페이스에 서비스를 배포하는 표준 절차:

### Steps
1. `kubectl apply -f deployment.yaml --namespace production`
2. `kubectl rollout status deployment/my-service --namespace production`
3. `kubectl get pods --namespace production` 로 Pod 상태 확인
4. ImagePullBackOff 발생 시: `kubectl describe pod <name> --namespace production` 로 원인 파악
   - 레지스트리 인증 문제인 경우 `kubectl create secret` 으로 imagePullSecret 생성

### 주요 주의사항
- 배포 전 반드시 `kubectl diff` 로 변경사항 확인
- production 배포 후 반드시 `kubectl rollout status` 로 완료 확인
- 실패 시 `kubectl rollout undo deployment/my-service` 로 즉시 롤백
