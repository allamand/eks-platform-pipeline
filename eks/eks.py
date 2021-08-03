from typing import cast

import requests
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_eks as eks
from aws_cdk import aws_iam as iam
from aws_cdk import core as cdk

# Flags const
DEPLOY_CLUSTER_AUTOSCALER = "deploy_cluster_autoscaler"
DEPLOY_AWS_LB_CONTROLLER = "deploy_alb_controller"

# Params const list
CLUSTER_NAME = "cluster_name"
ENV = "env_name"

# AWS LB Controller
AWS_LB_CONTROLLER = "aws-load-balancer-controller"


class PlatformEKS(cdk.Construct):
    def __init__(
            self,
            scope: cdk.Construct,
            id: str,
            *,
            vpc: ec2.Vpc,
            env: cdk.Environment,
            **kwargs):
        super().__init__(scope, id)

        self.flags_names = [
            DEPLOY_CLUSTER_AUTOSCALER,
            DEPLOY_AWS_LB_CONTROLLER
        ]

        self.params_names = [
            CLUSTER_NAME,
            ENV
        ]

        # extract flags from kwargs
        self.flags = self._extract_flags_from_kwargs(**kwargs)
        self.params = self._extract_params_from_kwargs(**kwargs)

        self.env = env
        self.eks_vpc = vpc
        self.eks_cluster = self._create_eks()
        self._create_nodegroups()
        self._deploy_addons()

    def _extract_params_from_kwargs(self, **kwargs: dict) -> dict:
        parameters = dict()
        for param in self.params_names:
            parameters[param] = param
            if param in kwargs:
                parameters[param] = kwargs.get(param)

        return parameters

    def _extract_flags_from_kwargs(self, **kwargs: dict) -> dict:
        flags = dict()
        for flag in self.flags_names:
            flags[flag] = True  # default all flags to True
            if flag in kwargs:
                flags[flag] = kwargs.get(flag)
        return flags

    def _create_eks(self, **kwargs) -> eks.Cluster:

        # Create IAM Role For EC2 bastion instance to be able to manage the cluster
        self.cluster_admin_role = iam.Role(self, "ClusterAdminRole",
                                           assumed_by=cast(
                                               iam.IPrincipal,
                                               iam.CompositePrincipal(
                                                   iam.AccountRootPrincipal(),
                                                   iam.ServicePrincipal("ec2.amazonaws.com")
                                               )
                                           )
                                           )
        cluster_admin_policy_statement_json_1 = {
            "Effect": "Allow",
            "Action": [
                "eks:DescribeCluster"
            ],
            "Resource": "*"
        }
        self.cluster_admin_role.add_to_policy(iam.PolicyStatement.from_json(cluster_admin_policy_statement_json_1))

        # Create SecurityGroup for the Control Plane ENIs
        eks_security_group = ec2.SecurityGroup(
            self,
            "EKSSecurityGroup",
            vpc=cast(ec2.IVpc, self.eks_vpc),
            allow_all_outbound=True,
        )

        eks_security_group.add_ingress_rule(
            # ec2.Peer.ipv4("10.0.0.0/16"), ec2.Port.all_traffic()
            ec2.Peer.ipv4(self.eks_vpc.vpc_cidr_block), ec2.Port.all_traffic()
        )

        # Create an EKS Cluster
        eks_cluster = eks.Cluster(
            self,
            "cluster",
            cluster_name=self.params[CLUSTER_NAME] + "-" + self.params[ENV],
            vpc=cast(ec2.IVpc, self.eks_vpc),
            # Use /28 subnets for the Control plane cross account ENIs
            # as recommended in https://docs.aws.amazon.com/eks/latest/userguide/network_reqs.html
            vpc_subnets=[ec2.SubnetSelection(subnet_group_name="eks-control-plane")],
            masters_role=cast(iam.IRole, self.cluster_admin_role),
            default_capacity=0,
            security_group=cast(ec2.ISecurityGroup, eks_security_group),
            endpoint_access=eks.EndpointAccess.PRIVATE,
            version=eks.KubernetesVersion.V1_20,
        )

        return eks_cluster

    def _create_nodegroups(self) -> None:

        required_nodegroup_managed_policy = [
            iam.ManagedPolicy.from_aws_managed_policy_name(
                managed_policy_name="AmazonSSMManagedInstanceCore"),
            iam.ManagedPolicy.from_aws_managed_policy_name(
                managed_policy_name="AmazonEKSWorkerNodePolicy"),
            iam.ManagedPolicy.from_aws_managed_policy_name(
                managed_policy_name="AmazonEKS_CNI_Policy"),
            iam.ManagedPolicy.from_aws_managed_policy_name(
                managed_policy_name="AmazonEC2ContainerRegistryReadOnly"),
        ]
        # Create IAM Role For node groups
        od_default_ng_role = iam.Role(self, "ODDefaultNGRole",
                                      assumed_by=cast(iam.IPrincipal, iam.CompositePrincipal(iam.AccountRootPrincipal(),
                                                                                             iam.ServicePrincipal(
                                                                                                 "ec2.amazonaws.com")
                                                                                             )),
                                      managed_policies=required_nodegroup_managed_policy,
                                      )
        spot_default_ng_role = iam.Role(self, "SPOTDefaultNGRole",
                                        assumed_by=cast(iam.IPrincipal,
                                                        iam.CompositePrincipal(iam.AccountRootPrincipal(),
                                                                               iam.ServicePrincipal(
                                                                                   "ec2.amazonaws.com")
                                                                               )),
                                        managed_policies=required_nodegroup_managed_policy,
                                        )
        od_graviton_ng_role = iam.Role(self, "ODGravitonNGRole",
                                       assumed_by=cast(iam.IPrincipal,
                                                       iam.CompositePrincipal(iam.AccountRootPrincipal(),
                                                                              iam.ServicePrincipal(
                                                                                  "ec2.amazonaws.com")
                                                                              )),
                                       managed_policies=required_nodegroup_managed_policy,
                                       )

        # On Demand subnets nodegroup
        self.eks_cluster.add_nodegroup_capacity(
            "ODDefaultNodegroup",
            nodegroup_name="od-default-ng",
            capacity_type=eks.CapacityType.ON_DEMAND,
            min_size=0,
            desired_size=1,
            max_size=10,
            ami_type=eks.NodegroupAmiType.AL2_X86_64,
            instance_types=[
                ec2.InstanceType("m5.large"),
            ],
            node_role=od_default_ng_role,
            subnets=ec2.SubnetSelection(subnet_group_name="Private")
        )
        # Spot subnets nodegroup
        self.eks_cluster.add_nodegroup_capacity(
            "SpotDefaultNodegroup",
            nodegroup_name="spot-default-ng",
            capacity_type=eks.CapacityType.SPOT,
            min_size=0,
            desired_size=1,
            max_size=10,
            ami_type=eks.NodegroupAmiType.AL2_X86_64,
            instance_types=[
                ec2.InstanceType("m5.large"),
                ec2.InstanceType("c5.large"),
                ec2.InstanceType("m4.large"),
                ec2.InstanceType("c4.large"),
            ],
            node_role=spot_default_ng_role,
            subnets=ec2.SubnetSelection(subnet_group_name="Private")
        )
        # Graviton subnets nodegroup
        self.eks_cluster.add_nodegroup_capacity(
            "ODGravitonNodegroup",
            nodegroup_name="od-graviton-ng",
            capacity_type=eks.CapacityType.SPOT,
            min_size=0,
            desired_size=0,
            max_size=10,
            ami_type=eks.NodegroupAmiType.AL2_ARM_64,
            instance_types=[
                ec2.InstanceType("m6g.large"),
            ],
            node_role=od_graviton_ng_role,
            subnets=ec2.SubnetSelection(subnet_group_name="Private")
        )

        # TODO: add nodegroups for GPU
        return

    def _create_fargate_profile(self) -> None:
        self.eks_cluster.add_fargate_profile(
            "DefaultFargateProfile",
            selectors=[eks.Selector(
                namespace="default",
                labels={"fargate": "enabled"}
            )],
            fargate_profile_name="default-fp",
            subnet_selection=ec2.SubnetSelection(subnet_group_name="Private")
        )
        return

    def _deploy_addons(self) -> None:
        self._deploy_ssm_agent()

        self._deploy_bastion()

        if self.flags[DEPLOY_CLUSTER_AUTOSCALER] is True:
            self._deploy_cluster_autoscaler()

        if self.flags[DEPLOY_AWS_LB_CONTROLLER] is True:
            self._deploy_aws_load_balancer_controller()

        return

    def _deploy_cluster_autoscaler(self) -> None:
        ca_sa_name = "cluster-autoscaler"
        cluster_autoscaler_service_account = self.eks_cluster.add_service_account(
            "cluster_autoscaler",
            name=ca_sa_name,
            namespace="kube-system"
        )
        # Create the PolicyStatements to attach to the role
        cluster_autoscaler_policy_statement = {
            "Effect": "Allow",
            "Action": [
                "autoscaling:DescribeAutoScalingGroups",
                "autoscaling:DescribeAutoScalingInstances",
                "autoscaling:DescribeLaunchConfigurations",
                "autoscaling:DescribeTags",
                "autoscaling:SetDesiredCapacity",
                "autoscaling:TerminateInstanceInAutoScalingGroup",
                "ec2:DescribeLaunchTemplateVersions"
            ],
            "Resource": "*"
        }

        # Attach the necessary permissions
        cluster_autoscaler_service_account.add_to_policy(
            iam.PolicyStatement.from_json(cluster_autoscaler_policy_statement))
        # Set CA for priority expander
        self.eks_cluster.add_manifest(
            "CAPriorityExpanderConfigMap",
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {
                    "name": "cluster-autoscaler-priority-expander",
                    "namespace": "kube-system"
                },
                "data": {
                    "priorities": "10: \n  - \n  - od*\n20: \n  - spot*"
                }
            }
        )
        # Install the Cluster Autoscaler
        # For more info see https://github.com/kubernetes/autoscaler
        cluster_autoscaler_chart = self.eks_cluster.add_helm_chart(
            "cluster-autoscaler",
            chart="cluster-autoscaler",
            version="9.9.2",
            release="cluster-autoscaler",
            repository="https://kubernetes.github.io/autoscaler",
            namespace="kube-system",
            values={
                "autoDiscovery": {
                    "clusterName": self.eks_cluster.cluster_name
                },
                "awsRegion": self.env.region,
                "resources": {
                    "requests": {
                        "cpu": "1",
                        "memory": "512Mi",
                    },
                    "limits": {
                        "cpu": "1",
                        "memory": "512Mi",
                    }
                },
                "rbac": {
                    "serviceAccount": {
                        "create": False,
                        "name": ca_sa_name
                    }
                },
                "extraArgs": {
                    "expander": "priority",
                    "max-node-provision-time": "5m0s"
                },
                "replicaCount": 1
            }
        )
        cluster_autoscaler_chart.node.add_dependency(self.ssm_agent_manifest)
        return

    def _deploy_aws_load_balancer_controller(self):
        aws_lb_controller_service_account = self.eks_cluster.add_service_account(
            "aws-load-balancer-controller",
            name=AWS_LB_CONTROLLER,
            namespace="kube-system"
        )
        resp = requests.get(
            "https://raw.githubusercontent.com/kubernetes-sigs/aws-load-balancer-controller/v2.2.0/docs/install/iam_policy.json")
        aws_load_balancer_controller_policy = resp.json()

        for stmt in aws_load_balancer_controller_policy["Statement"]:
            aws_lb_controller_service_account.add_to_principal_policy(iam.PolicyStatement.from_json(stmt))

        # Deploy the AWS Load Balancer Controller from the AWS Helm Chart
        # For more info check out https://github.com/aws/eks-charts/tree/master/stable/aws-load-balancer-controller
        aws_lb_controller_chart = self.eks_cluster.add_helm_chart(
            "aws-load-balancer-controller",
            chart="aws-load-balancer-controller",
            version="1.2.3",
            release="aws-lb-controller",
            repository="https://aws.github.io/eks-charts",
            namespace="kube-system",
            values={
                "clusterName": self.eks_cluster.cluster_name,
                "region": self.env.region,
                "vpcId": self.eks_vpc.vpc_id,
                "serviceAccount": {
                    "create": False,
                    "name": AWS_LB_CONTROLLER
                },
                "replicaCount": 2
            }
        )
        aws_lb_controller_chart.node.add_dependency(aws_lb_controller_service_account)
        return

    def _deploy_bastion(self):
        # Create an Instance Profile for our Admin Role to assume w/EC2
        cluster_admin_role_instance_profile = iam.CfnInstanceProfile(
            self, "ClusterAdminRoleInstanceProfile",
            roles=[self.cluster_admin_role.role_name]
        )
        # cluster_admin_role_instance_profile.node.add_dependency(self.cluster_admin_role)
        cluster_admin_role_instance_profile.node.add_dependency(self.cluster_admin_role)

        # Another way into our Bastion is via Systems Manager Session Manager
        self.cluster_admin_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore"))

        # policy to retrieve GitHub secrets from secretsmanager for Flux boostrap command
        bastion_secrets_manager_policy = {
            "Effect": "Allow",
            "Action": "secretsmanager:GetSecretValue",
            "Resource": [
                "arn:aws:secretsmanager:eu-west-1:*:secret:github-token*",
                "arn:aws:secretsmanager:eu-west-1:*:secret:github-user*"
            ],

        }

        self.cluster_admin_role.add_to_policy(iam.PolicyStatement.from_json(bastion_secrets_manager_policy))

        # Create code-server bastion
        # Get Latest Amazon Linux AMI
        amazon_linux_2 = ec2.MachineImage.latest_amazon_linux(
            generation=ec2.AmazonLinuxGeneration.AMAZON_LINUX_2,
            edition=ec2.AmazonLinuxEdition.STANDARD,
            virtualization=ec2.AmazonLinuxVirt.HVM,
            storage=ec2.AmazonLinuxStorage.GENERAL_PURPOSE
        )

        # Create SecurityGroup for code-server
        bastion_security_group = ec2.SecurityGroup(
            self, "BastionSecurityGroup",
            vpc=self.eks_vpc,
            allow_all_outbound=True
        )
        bastion_security_group.add_ingress_rule(
            ec2.Peer.any_ipv4(),
            ec2.Port.tcp(8080)
        )

        # Add a rule to allow our new SG to talk to the EKS control plane
        self.eks_cluster.cluster_security_group.add_ingress_rule(
            bastion_security_group,
            ec2.Port.all_traffic()
        )

        # Create our Bastion EC2 instance running CodeServer

        self.bastion = ec2.Instance(
            self, "EKSBastion",
            instance_type=ec2.InstanceType("t3.large"),
            machine_image=amazon_linux_2,
            role=self.cluster_admin_role,
            vpc=self.eks_vpc,
            instance_name=self.eks_cluster.cluster_name + "-bastion",
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            security_group=bastion_security_group,
            block_devices=[ec2.BlockDevice(device_name="/dev/xvda", volume=ec2.BlockDeviceVolume.ebs(20))]
        )

        # Add UserData
        self.bastion.user_data.add_commands("yum -y install perl-Digest-SHA")
        self.bastion.user_data.add_commands("mkdir -p ~/.local/lib ~/.local/bin ~/.config/code-server")
        self.bastion.user_data.add_commands(
            "curl -fL https://github.com/cdr/code-server/releases/download/v3.9.1/code-server-3.9.1-linux-amd64.tar.gz | tar -C ~/.local/lib -xz")
        self.bastion.user_data.add_commands(
            "mv ~/.local/lib/code-server-3.9.1-linux-amd64 ~/.local/lib/code-server-3.9.1")
        self.bastion.user_data.add_commands(
            "ln -s ~/.local/lib/code-server-3.9.1/bin/code-server ~/.local/bin/code-server")
        self.bastion.user_data.add_commands(
            "echo \"bind-addr: 0.0.0.0:8080\" > ~/.config/code-server/config.yaml")
        self.bastion.user_data.add_commands("echo \"auth: password\" >> ~/.config/code-server/config.yaml")
        self.bastion.user_data.add_commands(
            "echo \"password: $(curl -s http://169.254.169.254/latest/meta-data/instance-id)\" >> ~/.config/code-server/config.yaml")
        self.bastion.user_data.add_commands("echo \"cert: false\" >> ~/.config/code-server/config.yaml")
        self.bastion.user_data.add_commands("~/.local/bin/code-server &")
        self.bastion.user_data.add_commands(
            "echo \"/root/.local/bin/code-server &\" >> /etc/rc.d/rc.local")
        self.bastion.user_data.add_commands("chmod a+x /etc/rc.d/rc.local")
        self.bastion.user_data.add_commands(
            "curl -o kubectl https://amazon-eks.s3.us-west-2.amazonaws.com/1.19.6/2021-01-05/bin/linux/amd64/kubectl")
        self.bastion.user_data.add_commands("chmod +x ./kubectl")
        self.bastion.user_data.add_commands("mv ./kubectl /usr/bin")
        self.bastion.user_data.add_commands("curl https://intoli.com/install-google-chrome.sh | bash")
        self.bastion.user_data.add_commands(
            "~/.local/bin/code-server --install-extension auchenberg.vscode-browser-preview")
        self.bastion.user_data.add_commands(
            "aws eks update-kubeconfig --name " + self.eks_cluster.cluster_name + " --region " + self.env.region)

        self.bastion.user_data.add_commands("PATH=$PATH:/usr/local/bin")
        self.bastion.user_data.add_commands("export KUBECONFIG=~/.kube/config")
        self.bastion.user_data.add_commands("curl -s https://fluxcd.io/install.sh | sudo bash")
        self.bastion.user_data.add_commands("echo 'PATH=$PATH:/usr/local/bin' >> ~/.bash_profile")
        self.bastion.user_data.add_commands("echo '. <(flux completion bash)' >> ~/.bash_profile")

        # bootstrap flux using the bastion user-data

        self.bastion.user_data.add_commands(
            "export GITHUB_TOKEN=$(aws --region {region} secretsmanager get-secret-value --secret-id github-token --query 'SecretString' --output text)".format(
                region=self.env.region))
        self.bastion.user_data.add_commands(
            "export GITHUB_USER=$(aws --region {region} secretsmanager get-secret-value --secret-id github-user --query 'SecretString' --output text)".format(
                region=self.env.region))
        self.bastion.user_data.add_commands("KUBECONFIG=~/.kube/config flux bootstrap github \
                                                      --owner=$GITHUB_USER \
                                                      --repository=flux-system-eks \
                                                      --path=clusters/{cluster_name} \
                                                      --personal".format(cluster_name=self.eks_cluster.cluster_name)
                                            )

        # Output the Bastion address
        cdk.CfnOutput(
            self, "BastionAddress",
            value="http://" + self.bastion.instance_public_ip + ":8080",
            description="Address to reach your Bastion's VS Code Web UI",
        )
        # Wait to deploy Bastion until cluster is up and we're deploying manifests/charts to it
        # This could be any of the charts/manifests I just picked this one at random
        self.bastion.node.add_dependency(self.ssm_agent_manifest)

        return

    def _deploy_ssm_agent(self):
        # For more information see
        # https://docs.aws.amazon.com/prescriptive-guidance/latest/patterns/install-ssm-agent-on-amazon-eks-worker-nodes-by-using-kubernetes-daemonset.html
        self.ssm_agent_manifest = \
            self.eks_cluster.add_manifest("SSMAgentManifest",
                                          {
                                              "apiVersion": "apps/v1",
                                              "kind": "DaemonSet",
                                              "metadata": {
                                                  "labels": {
                                                      "k8s-app": "ssm-installer"
                                                  },
                                                  "name": "ssm-installer",
                                                  "namespace": "kube-system"
                                              },
                                              "spec": {
                                                  "selector": {
                                                      "matchLabels": {
                                                          "k8s-app": "ssm-installer"
                                                      }
                                                  },
                                                  "template": {
                                                      "metadata": {
                                                          "labels": {
                                                              "k8s-app": "ssm-installer"
                                                          }
                                                      },
                                                      "spec": {
                                                          "containers": [
                                                              {
                                                                  "image": "amazonlinux",
                                                                  "imagePullPolicy": "Always",
                                                                  "name": "ssm",
                                                                  "command": [
                                                                      "/bin/bash"
                                                                  ],
                                                                  "args": [
                                                                      "-c",
                                                                      "echo '* * * * * root yum install -y https://s3.amazonaws.com/ec2-downloads-windows/SSMAgent/latest/linux_amd64/amazon-ssm-agent.rpm & rm -rf /etc/cron.d/ssmstart' > /etc/cron.d/ssmstart"
                                                                  ],
                                                                  "securityContext": {
                                                                      "allowPrivilegeEscalation": True
                                                                  },
                                                                  "volumeMounts": [
                                                                      {
                                                                          "mountPath": "/etc/cron.d",
                                                                          "name": "cronfile"
                                                                      }
                                                                  ],
                                                                  "terminationMessagePath": "/dev/termination-log",
                                                                  "terminationMessagePolicy": "File"
                                                              }
                                                          ],
                                                          "volumes": [
                                                              {
                                                                  "name": "cronfile",
                                                                  "hostPath": {
                                                                      "path": "/etc/cron.d",
                                                                      "type": "Directory"
                                                                  }
                                                              }
                                                          ],
                                                          "dnsPolicy": "ClusterFirst",
                                                          "restartPolicy": "Always",
                                                          "schedulerName": "default-scheduler",
                                                          "terminationGracePeriodSeconds": 30
                                                      }
                                                  }
                                              }
                                          })
