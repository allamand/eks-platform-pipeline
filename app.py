#!/usr/bin/env python3
import os

# For consistency with TypeScript code, `cdk` is the preferred import name for
# the CDK's core module.  The following line also imports it as `core` for use
# with examples from the CDK Developer's Guide, which are in the process of
# being updated to use `cdk`.  You may delete this import if you don't need it.
from aws_cdk import core
from aws_cdk import core as cdk

# from eks_platform.eks_platform_stack import EksPlatformStack
from deployment import Platform
from pipeline import Pipeline

dev_env = cdk.Environment(
    account=os.environ["CDK_DEFAULT_ACCOUNT"],
    region=os.environ["CDK_DEFAULT_REGION"]
)

pipeline_env = cdk.Environment(
    account=os.environ["CDK_DEFAULT_ACCOUNT"],
    region=os.environ["CDK_DEFAULT_REGION"]

)
app = core.App()

Platform(app,
         f"{Platform.__name__}-Dev",
         env=dev_env,
         env_name="dev",
         cluster_name="eks-test"
         )

Pipeline(app, f"{Platform.__name__}-Pipeline", env=pipeline_env)

app.synth()
