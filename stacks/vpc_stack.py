#!/usr/bin/env python
# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.
"""
VPC and networking stack for OSCAR Slack Bot.

This module defines the VPC configuration, security groups, and VPC endpoints
used by the OSCAR Slack Bot infrastructure. It imports existing VPC resources
and configures networking for Lambda functions with OpenSearch access.
"""

import logging

from aws_cdk import Stack
from aws_cdk import aws_ec2 as ec2
from constructs import Construct

logger = logging.getLogger(__name__)


class OscarVpcStack(Stack):
    """
    VPC and networking resources for OSCAR Slack Bot.
    This construct imports existing VPC resources and configures security groups,
    VPC endpoints, and network ACLs for proper isolation and secure access to
    AWS services and OpenSearch clusters.
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        """
        Initialize VPC and networking resources.
        Args:
            scope: The CDK construct scope
            construct_id: The ID of the construct
            **kwargs: Additional arguments for Stack
        """
        super().__init__(scope, construct_id, **kwargs)

        # Import existing VPC configuration
        self.vpc: ec2.IVpc = self._configure_vpc()
        self.lambda_security_group: ec2.ISecurityGroup = self._create_lambda_security_group()

        # Create VPC endpoints for STS and Secrets Manager
        self._create_vpc_endpoints()

    def _configure_vpc(self) -> ec2.IVpc:
        """
        Import the existing VPC configuration.
        Returns:
            The imported VPC
        """
        # Use the VPC ID from .env file
        vpc_id = self.node.try_get_context("VPC_ID")

        if not vpc_id:
            try:
                logging.info("VPC_ID environment variable not found. A new VPC will be created")
                vpc: ec2.IVpc = ec2.Vpc(
                    self, "OscarVpc",
                    max_azs=6,
                    subnet_configuration=[
                        ec2.SubnetConfiguration(
                            name="public",
                            subnet_type=ec2.SubnetType.PUBLIC,
                            map_public_ip_on_launch=True,
                        )
                    ],
                )
                return vpc
            except Exception:
                logger.error("Failed to create VPC with given CIDR.")
                raise ValueError("Could not create VPC. Please check your account for details.")
        else:
            try:
                vpc = ec2.Vpc.from_lookup(
                    self, "ExistingVpc",
                    vpc_id=vpc_id
                )

                logger.info(f"Successfully imported VPC: {vpc_id}")
                return vpc

            except Exception as e:
                logger.error(f"Failed to import VPC {vpc_id}: {e}")
                raise ValueError(f"Could not import VPC {vpc_id}. Please verify the VPC_ID in your .env file.")

    def _create_lambda_security_group(self) -> ec2.ISecurityGroup:
        """
        Create or import security group for Lambda functions with OpenSearch access.
        Returns:
            The Lambda security group
        """
        # Try to import existing security group first
        existing_sg_id = self.node.try_get_context("LAMBDA_SECURITY_GROUP_ID")

        if existing_sg_id:
            try:
                logger.info(f"Importing existing security group: {existing_sg_id}")
                return ec2.SecurityGroup.from_security_group_id(
                    self, "ExistingLambdaSecurityGroup",
                    security_group_id=existing_sg_id
                )
            except Exception as e:
                logger.warning(f"Failed to import security group {existing_sg_id}: {e}")
                logger.info("Creating new security group")

        # Create new security group
        security_group = ec2.SecurityGroup(
            self, "OscarLambdaSecurityGroup",
            vpc=self.vpc,
            description="Security group for OSCAR Lambda functions with OpenSearch access",
            allow_all_outbound=True,
        )

        # Inbound: all traffic from itself (Lambda <-> VPC endpoints)
        security_group.add_ingress_rule(
            peer=security_group,
            connection=ec2.Port.all_traffic(),
        )

        # Inbound: HTTPS from VPC CIDR
        security_group.add_ingress_rule(
            peer=ec2.Peer.ipv4(self.vpc.vpc_cidr_block),
            connection=ec2.Port.tcp(443),
            description=f"from {self.vpc.vpc_cidr_block}:443",
        )

        return security_group

    def _create_vpc_endpoints(self) -> None:
        """Create STS and Secrets Manager VPC endpoints for Lambda access."""
        subnet_selection = ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC)

        ec2.InterfaceVpcEndpoint(
            self, "STSVpcEndpoint",
            vpc=self.vpc,
            service=ec2.InterfaceVpcEndpointAwsService.STS,
            subnets=subnet_selection,
            security_groups=[self.lambda_security_group],
            private_dns_enabled=True,
        )

        ec2.InterfaceVpcEndpoint(
            self, "SecretsManagerVpcEndpoint",
            vpc=self.vpc,
            service=ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER,
            subnets=subnet_selection,
            security_groups=[self.lambda_security_group],
            private_dns_enabled=True,
        )

    def _configure_network_acls(self) -> None:
        """
        Configure Network ACLs for additional security layers.
        This method creates custom Network ACLs with restrictive rules
        for enhanced security beyond security groups.
        """
        # Try to get private subnets for Lambda deployment
        private_subnets = []

        try:
            private_subnets = self.vpc.select_subnets(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ).subnets
        except Exception as e:
            logger.warning(f"No private subnets with egress found: {e}")

        if not private_subnets:
            try:
                # Fallback to isolated private subnets
                private_subnets = self.vpc.select_subnets(
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
                ).subnets
            except Exception as e:
                logger.warning(f"No isolated private subnets found: {e}")

        if not private_subnets:
            logger.warning("No private subnets found in VPC - skipping Network ACL configuration")
            return

        if private_subnets:
            # Create custom Network ACL for Lambda subnets
            lambda_nacl = ec2.NetworkAcl(
                self, "OscarLambdaNetworkAcl",
                vpc=self.vpc,
                network_acl_name="oscar-lambda-nacl"
            )

            # Allow outbound HTTPS traffic
            lambda_nacl.add_entry(
                "AllowOutboundHTTPS",
                cidr=ec2.AclCidr.any_ipv4(),
                rule_number=100,
                traffic=ec2.AclTraffic.tcp_port(443),
                direction=ec2.TrafficDirection.EGRESS,
                rule_action=ec2.Action.ALLOW
            )

            # Allow outbound HTTP traffic
            lambda_nacl.add_entry(
                "AllowOutboundHTTP",
                cidr=ec2.AclCidr.any_ipv4(),
                rule_number=110,
                traffic=ec2.AclTraffic.tcp_port(80),
                direction=ec2.TrafficDirection.EGRESS,
                rule_action=ec2.Action.ALLOW
            )

            # Allow outbound DNS
            lambda_nacl.add_entry(
                "AllowOutboundDNS",
                cidr=ec2.AclCidr.any_ipv4(),
                rule_number=120,
                traffic=ec2.AclTraffic.udp_port(53),
                direction=ec2.TrafficDirection.EGRESS,
                rule_action=ec2.Action.ALLOW
            )

            # Allow outbound OpenSearch
            lambda_nacl.add_entry(
                "AllowOutboundOpenSearch",
                cidr=ec2.AclCidr.any_ipv4(),
                rule_number=130,
                traffic=ec2.AclTraffic.tcp_port(9200),
                direction=ec2.TrafficDirection.EGRESS,
                rule_action=ec2.Action.ALLOW
            )

            # Allow inbound ephemeral ports for responses
            lambda_nacl.add_entry(
                "AllowInboundEphemeral",
                cidr=ec2.AclCidr.any_ipv4(),
                rule_number=100,
                traffic=ec2.AclTraffic.tcp_port_range(1024, 65535),
                direction=ec2.TrafficDirection.INGRESS,
                rule_action=ec2.Action.ALLOW
            )

            # Associate Network ACL with private subnets (first few subnets)
            for i, subnet in enumerate(private_subnets[:3]):  # Limit to first 3 subnets
                ec2.SubnetNetworkAclAssociation(
                    self, f"LambdaSubnetAssociation{i}",
                    network_acl=lambda_nacl,
                    subnet=subnet
                )

    # @property
    # def vpc_config_for_lambda(self) -> dict:
    #     """
    #     Get VPC configuration dictionary for Lambda functions.
    #
    #     Returns:
    #         Dictionary with VPC configuration for Lambda deployment
    #     """
    #     private_subnets = self.vpc.select_subnets(
    #         subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
    #     ).subnets
    #
    #     if not private_subnets:
    #         private_subnets = self.vpc.select_subnets(
    #             subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
    #         ).subnets
    #
    #     return {
    #         "vpc": self.vpc,
    #         "subnets": private_subnets,
    #         "security_groups": [self.lambda_security_group]
    #     }

    # def get_subnet_ids(self, subnet_type: str = "private") -> List[str]:
    #     """
    #     Get subnet IDs for the specified subnet type.
    #
    #     Args:
    #         subnet_type: Type of subnets to retrieve ("private", "public", "isolated")
    #
    #     Returns:
    #         List of subnet IDs
    #     """
    #     if subnet_type == "private":
    #         subnets = self.vpc.select_subnets(
    #             subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
    #         ).subnet_ids
    #
    #         if not subnets:
    #             subnets = self.vpc.select_subnets(
    #                 subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
    #             ).subnet_ids
    #
    #     elif subnet_type == "public":
    #         subnets = self.vpc.select_subnets(
    #             subnet_type=ec2.SubnetType.PUBLIC
    #         ).subnet_ids
    #
    #     elif subnet_type == "isolated":
    #         subnets = self.vpc.select_subnets(
    #             subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
    #         ).subnet_ids
    #
    #     else:
    #         raise ValueError(f"Invalid subnet type: {subnet_type}")
    #
    #     return subnets
