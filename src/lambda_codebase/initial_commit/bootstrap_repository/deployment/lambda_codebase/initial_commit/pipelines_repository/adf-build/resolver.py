# Copyright 2019 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""This file is pulled into CodeBuild containers
   and used to resolve values from Parameter Store and CloudFormation
"""
import os
import boto3

from s3 import S3
from parameter_store import ParameterStore
from cloudformation import CloudFormation
from sts import STS
from logger import configure_logger

LOGGER = configure_logger(__name__)
DEFAULT_REGION = os.environ["AWS_REGION"]
S3_BUCKET_NAME = os.environ["S3_BUCKET_NAME"]

class Resolver:
    def __init__(self, parameter_store, stage_parameters, comparison_parameters):
        self.parameter_store = parameter_store
        self.stage_parameters = stage_parameters
        self.comparison_parameters = comparison_parameters
        self.s3 = S3(DEFAULT_REGION, S3_BUCKET_NAME)
        self.sts = STS()

    def fetch_stack_output(self, value, param, key=None):
        try:
            [_, account_id, region, stack_name, export] = str(value).split(':')
        except ValueError:
            raise ValueError(
                "{0} is not a valid import string."
                "syntax should be import:account_id:region:stack_name:export_key".format(str(value))
            )

        LOGGER.info("Assuming the role %s", 'arn:aws:iam::{0}:role/{1}'.format(
            account_id,
            'adf-cloudformation-deployment-role'
            ))
        role = self.sts.assume_cross_account_role(
            'arn:aws:iam::{0}:role/{1}'.format(
                account_id,
                'adf-cloudformation-deployment-role'),
            'importer'
        )
        cloudformation = CloudFormation(
            region=region,
            deployment_account_region=os.environ["AWS_REGION"],
            role=role,
            stack_name=stack_name,
            account_id=account_id
        )
        LOGGER.info("Retrieving value of key %s from %s on %s in %s", export, stack_name, account_id, region)
        stack_output = cloudformation.get_stack_output(export)
        if not stack_output:
            raise Exception("No Key was found on {0} with the name {1}".format(stack_name, export))

        LOGGER.info("Stack output value is %s", stack_output)
        if key:
            self.stage_parameters[key][param] = stack_output
            return
        self.stage_parameters[key] = stack_output

    def upload(self, value, key, file_name, param=None):
        if str(value).count(':') > 1:
            [_, region, value] = value.split(':')
            bucket_name = self.parameter_store.fetch_parameter(
                '/cross_region/s3_regional_bucket/{0}'.format(region)
            )
            regional_client = S3(region, bucket_name)
            LOGGER.info("Uploading %s as %s to S3 Bucket %s in %s", value, file_name, bucket_name, region)
            if param:
                self.stage_parameters[param][key] = regional_client.put_object(
                    "adf-upload/{0}/{1}".format(value, file_name),
                    "{0}".format(value)
                )
            else:
                self.stage_parameters[key] = regional_client.put_object(
                    "adf-upload/{0}/{1}".format(value, file_name),
                    "{0}".format(value)
                )
            return True
        [_, value] = value.split(':')
        LOGGER.info("Uploading %s to S3", value)
        if param:
            self.stage_parameters[param][key] = self.s3.put_object(
                "adf-upload/{0}/{1}".format(value, file_name),
                "{0}".format(value)
            )
        else:
            self.stage_parameters[key] = self.s3.put_object(
                "adf-upload/{0}/{1}".format(value, file_name),
                "{0}".format(value)
            )
        return False

    def fetch_parameter_store_value(self, value, key, param=None):
        if str(value).count(':') > 1:
            [_, region, value] = value.split(':')
            regional_client = ParameterStore(region, boto3)
            LOGGER.info("Fetching Parameter from %s", value)
            if param:
                self.stage_parameters[param][key] = regional_client.fetch_parameter(
                    value
                )
            else:
                self.stage_parameters[key] = regional_client.fetch_parameter(
                    value
                )
            return True
        [_, value] = value.split(':')
        LOGGER.info("Fetching Parameter from %s", value)
        if param:
            self.stage_parameters[param][key] = self.parameter_store.fetch_parameter(
                value
            )
        else:
            self.stage_parameters[key] = self.parameter_store.fetch_parameter(
                value
            )
        return False

    def update_cfn(self, key, param):
        if key not in self.stage_parameters[param]:
            self.stage_parameters[param][key] = self.comparison_parameters[param][key]

    def update_sc(self, key):
        if key not in self.stage_parameters:
            self.stage_parameters[key] = self.comparison_parameters[key]
