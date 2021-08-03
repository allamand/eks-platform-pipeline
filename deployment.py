from typing import Any

from aws_cdk import core as cdk

from eks.eks import PlatformEKS
from network.infra import PlatformNetwork


class Platform(cdk.Stage):
    # pylint: disable=redefined-builtin
    # The 'id' parameter name is CDK convention.
    def __init__(
            self,
            scope: cdk.Construct,
            id_: str,
            *,
            env: cdk.Environment,
            outdir: str = None,
            env_name: str = "eks-env",
            **kwargs: Any

    ):
        super().__init__(scope, id_, env=env, outdir=outdir)

        platform_network_stack = cdk.Stack(self, "Network")
        network = PlatformNetwork(
            platform_network_stack,
            "PlatformNetwork")

        cluster_name = "eks"
        if "cluster_name" in kwargs:
            cluster_name = kwargs.get("cluster_name")

        platform_eks_stack = cdk.Stack(self, "EKS")
        PlatformEKS(scope=platform_eks_stack,
                    id="PlatformEKS",
                    vpc=network.vpc,
                    env=env,
                    env_name=env_name,
                    cluster_name=cluster_name
                    )
