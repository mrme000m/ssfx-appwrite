#!/usr/bin/env bash
#
# Create a single AWS EC2 instance using the 'bs00' AWS CLI profile.
# The script relies on the default credentials/API key configured for
# the 'bs00' profile (aws configure --profile bs00).
#
# Defaults:
#   - Instance type: c7i-flex.large (2 vCPU / 4 GiB)
#     This type is Free Tier eligible in the current account.
#     Override with INSTANCE_TYPE if you need a different size.
#   - Root volume: 50 GB gp3 SSD
#   - Key pair: default-api-key (created if missing)
#   - SSH access: current public IP /24 by default
#   - Extra public ports: configurable via PUBLIC_PORTS (comma-separated)
#
# Usage:
#   ./infra/aws/create-vm.sh
#   SSH_CIDR=0.0.0.0/0 ./infra/aws/create-vm.sh
#   PUBLIC_PORTS=80,443,8080 ./infra/aws/create-vm.sh

set -euo pipefail

AWS_PROFILE="${AWS_PROFILE:-bs00}"
REGION="${AWS_REGION:-us-east-1}"
INSTANCE_TYPE="${INSTANCE_TYPE:-c7i-flex.large}"
KEY_NAME="${KEY_NAME:-default-api-key}"
VM_NAME="${VM_NAME:-ssfx-aws-vm}"
ROOT_VOLUME_GB="${ROOT_VOLUME_GB:-50}"
SSH_CIDR="${SSH_CIDR:-}"
PUBLIC_PORTS="${PUBLIC_PORTS:-}"

echo "==> AWS profile: ${AWS_PROFILE}"
echo "==> Region: ${REGION}"
echo "==> Instance type: ${INSTANCE_TYPE}"
echo "==> Root volume: ${ROOT_VOLUME_GB} GB gp3"

aws="aws --profile ${AWS_PROFILE} --region ${REGION}"

# ---------------------------------------------------------------------------
# Helper: ensure a security group ingress rule exists.
# ---------------------------------------------------------------------------
ensure_ingress() {
  local sg_id="$1" proto="$2" port="$3" cidr="$4"
  local has
  has=$(${aws} ec2 describe-security-groups \
    --group-ids "${sg_id}" \
    --query "SecurityGroups[0].IpPermissions[?FromPort==\`${port}\` && ToPort==\`${port}\` && IpProtocol==\`${proto}\`].IpRanges[*].CidrIp" \
    --output text | grep -F "${cidr}" || true)
  if [[ -z "${has}" ]]; then
    ${aws} ec2 authorize-security-group-ingress \
      --group-id "${sg_id}" \
      --protocol "${proto}" \
      --port "${port}" \
      --cidr "${cidr}" >/dev/null
  fi
}

# ---------------------------------------------------------------------------
# 1. Ensure key pair exists (used for SSH access).
# ---------------------------------------------------------------------------
if ! ${aws} ec2 describe-key-pairs --key-names "${KEY_NAME}" >/dev/null 2>&1; then
  echo "==> Creating key pair: ${KEY_NAME}"
  ${aws} ec2 create-key-pair \
    --key-name "${KEY_NAME}" \
    --key-type rsa \
    --key-format pem \
    --query 'KeyMaterial' \
    --output text > "${HOME}/.ssh/${KEY_NAME}.pem"
  chmod 600 "${HOME}/.ssh/${KEY_NAME}.pem"
  echo "==> Private key saved to ${HOME}/.ssh/${KEY_NAME}.pem"
else
  echo "==> Using existing key pair: ${KEY_NAME}"
fi

# ---------------------------------------------------------------------------
# 2. Resolve default VPC and one subnet.
# ---------------------------------------------------------------------------
VPC_ID=$(${aws} ec2 describe-vpcs \
  --filters 'Name=isDefault,Values=true' \
  --query 'Vpcs[0].VpcId' \
  --output text)

if [[ "${VPC_ID}" == "None" || -z "${VPC_ID}" ]]; then
  echo "ERROR: No default VPC found in ${REGION}." >&2
  exit 1
fi
echo "==> Default VPC: ${VPC_ID}"

SUBNET_ID=$(${aws} ec2 describe-subnets \
  --filters "Name=vpc-id,Values=${VPC_ID}" 'Name=defaultForAz,Values=true' \
  --query 'Subnets[0].SubnetId' \
  --output text)

if [[ "${SUBNET_ID}" == "None" || -z "${SUBNET_ID}" ]]; then
  echo "ERROR: No default subnet found in VPC ${VPC_ID}." >&2
  exit 1
fi
echo "==> Subnet: ${SUBNET_ID}"

# ---------------------------------------------------------------------------
# 3. Create / reuse security group and open requested ports.
# ---------------------------------------------------------------------------
if [[ -z "${SSH_CIDR}" ]]; then
  MY_IP=$(curl -fsSL https://checkip.amazonaws.com)
  IP_PREFIX="${MY_IP%.*}"
  SSH_CIDR="${IP_PREFIX}.0/24"
fi

SG_NAME="${VM_NAME}-sg"

SG_ID=$(${aws} ec2 describe-security-groups \
  --filters "Name=group-name,Values=${SG_NAME}" "Name=vpc-id,Values=${VPC_ID}" \
  --query 'SecurityGroups[0].GroupId' \
  --output text 2>/dev/null || true)

if [[ "${SG_ID}" == "None" || -z "${SG_ID}" ]]; then
  echo "==> Creating security group: ${SG_NAME}"
  SG_ID=$(${aws} ec2 create-security-group \
    --group-name "${SG_NAME}" \
    --description "Access rules for ${VM_NAME}" \
    --vpc-id "${VPC_ID}" \
    --query 'GroupId' \
    --output text)
else
  echo "==> Using existing security group: ${SG_ID}"
  # Re-running from a different IP in the same /24 is common on dynamic home connections.
  HAS_RULE=$(${aws} ec2 describe-security-groups \
    --group-ids "${SG_ID}" \
    --query "SecurityGroups[0].IpPermissions[?FromPort==\`22\` && ToPort==\`22\` && IpProtocol==\`tcp\`].IpRanges[*].CidrIp" \
    --output text | grep -F "${SSH_CIDR}" || true)
  if [[ -z "${HAS_RULE}" ]]; then
    echo "==> Adding SSH ingress for ${SSH_CIDR}"
    ${aws} ec2 authorize-security-group-ingress \
      --group-id "${SG_ID}" \
      --protocol tcp \
      --port 22 \
      --cidr "${SSH_CIDR}" >/dev/null
  fi
fi

# SSH ingress
ensure_ingress "${SG_ID}" tcp 22 "${SSH_CIDR}"
echo "==> SSH allowed from: ${SSH_CIDR}"

# Optional public ports from 0.0.0.0/0
if [[ -n "${PUBLIC_PORTS}" ]]; then
  for port in ${PUBLIC_PORTS//,/ }; do
    ensure_ingress "${SG_ID}" tcp "${port}" "0.0.0.0/0"
    echo "==> Public port ${port} allowed from 0.0.0.0/0"
  done
fi

# ---------------------------------------------------------------------------
# 4. Resolve latest Amazon Linux 2023 AMI.
# ---------------------------------------------------------------------------
AMI_ID=$(${aws} ssm get-parameters \
  --names /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 \
  --query 'Parameters[0].Value' \
  --output text)
echo "==> AMI: ${AMI_ID}"

# ---------------------------------------------------------------------------
# 5. Launch the instance.
# ---------------------------------------------------------------------------
echo "==> Launching EC2 instance..."
INSTANCE_ID=$(${aws} ec2 run-instances \
  --image-id "${AMI_ID}" \
  --instance-type "${INSTANCE_TYPE}" \
  --key-name "${KEY_NAME}" \
  --security-group-ids "${SG_ID}" \
  --subnet-id "${SUBNET_ID}" \
  --block-device-mappings "[{
    \"DeviceName\":\"/dev/xvda\",
    \"Ebs\":{
      \"VolumeSize\":${ROOT_VOLUME_GB},
      \"VolumeType\":\"gp3\",
      \"DeleteOnTermination\":true
    }
  }]" \
  --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=${VM_NAME}}]" \
  --query 'Instances[0].InstanceId' \
  --output text)

echo "==> Instance ID: ${INSTANCE_ID}"
echo "==> Waiting for instance to reach running state..."
${aws} ec2 wait instance-running --instance-ids "${INSTANCE_ID}"

# ---------------------------------------------------------------------------
# 6. Fetch connection details.
# ---------------------------------------------------------------------------
PUBLIC_IP=$(${aws} ec2 describe-instances \
  --instance-ids "${INSTANCE_ID}" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' \
  --output text)

PRIVATE_IP=$(${aws} ec2 describe-instances \
  --instance-ids "${INSTANCE_ID}" \
  --query 'Reservations[0].Instances[0].PrivateIpAddress' \
  --output text)

echo ""
echo "==> VM created successfully"
echo "    Name:        ${VM_NAME}"
echo "    Instance ID: ${INSTANCE_ID}"
echo "    Public IP:   ${PUBLIC_IP}"
echo "    Private IP:  ${PRIVATE_IP}"
echo "    SSH key:     ${HOME}/.ssh/${KEY_NAME}.pem"
echo "    Connect:     ssh -i ${HOME}/.ssh/${KEY_NAME}.pem ec2-user@${PUBLIC_IP}"
