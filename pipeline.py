import json
import os
from pathlib import Path
from typing import Any

# import boto3
from aws_cdk import aws_codepipeline as codepipeline
from aws_cdk import aws_codepipeline_actions as codepipeline_actions
from aws_cdk import core as cdk
from aws_cdk import pipelines
from aws_cdk.core import SecretValue

from deployment import Platform


class Pipeline(cdk.Stack):
    # pylint: disable=redefined-builtin
    # The 'id' parameter name is CDK convention.
    def __init__(self, scope: cdk.Construct, id: str, **kwargs: Any):
        super().__init__(scope, id, **kwargs)

        source_artifact = codepipeline.Artifact()
        cloud_assembly_artifact = codepipeline.Artifact()

        source_action = codepipeline_actions.GitHubSourceAction(
            action_name="GitHub",
            output=source_artifact,
            # pylint: disable=line-too-long
            oauth_token=SecretValue.secrets_manager('github-token'),
            owner=SecretValue.secrets_manager('github-user').to_string(),
            repo="eks-platform",
            branch="main",
        )

        synth_action = pipelines.SimpleSynthAction(
            source_artifact=source_artifact,
            cloud_assembly_artifact=cloud_assembly_artifact,
            install_commands=[
                "pyenv local 3.7.10",
                "./scripts/install-deps.sh",
            ],
            synth_command="npx cdk synth",
        )

        cdk_pipeline = pipelines.CdkPipeline(
            self,
            "EKSPlatform",
            source_action=source_action,
            synth_action=synth_action,  # type: ignore
            single_publisher_per_type=True,
            cdk_cli_version=Pipeline._get_cdk_cli_version(),
            cloud_assembly_artifact=cloud_assembly_artifact,
        )

        self._add_pre_prod_stage(cdk_pipeline)
        self._add_prod_stage(cdk_pipeline)

    @staticmethod
    def _get_cdk_cli_version() -> str:
        package_json_path = Path(__file__).resolve().parent.joinpath("package.json")
        with open(package_json_path) as package_json_file:
            package_json = json.load(package_json_file)
        cdk_cli_version = str(package_json["devDependencies"]["aws-cdk"])
        return cdk_cli_version

    def _add_pre_prod_stage(self, cdk_pipeline: pipelines.CdkPipeline) -> None:
        pre_prod_env = cdk.Environment(
            account=os.environ["CDK_DEFAULT_ACCOUNT"],
            region=os.environ["CDK_DEFAULT_REGION"]
        )

        pre_prod_platform_stage = Platform(
            self,
            f"{Platform.__name__}-PreProd",
            env=pre_prod_env,
            env_name="pre-prod"
        )
        pre_prod_stage = cdk_pipeline.add_application_stage(pre_prod_platform_stage)
        pre_prod_stage.add_manual_approval_action(
            action_name="ConfirmPreProdDeployment",
            run_order=pre_prod_stage.next_sequential_run_order()
        )

    def _add_prod_stage(self, cdk_pipeline: pipelines.CdkPipeline) -> None:
        prod_env = cdk.Environment(
            account=os.environ["CDK_DEFAULT_ACCOUNT"],
            region="eu-central-1"
        )

        prod_platform_stage = Platform(
            self, f"{Platform.__name__}-Prod", env=prod_env
        )
        prod_stage = cdk_pipeline.add_application_stage(prod_platform_stage)
        _ = prod_stage
