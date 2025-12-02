"""
Simple VPC Component - Public Subnets Only

This module creates a VPC with only public subnets, suitable for:
- EC2 instances with direct internet access via Internet Gateway
- RDS instances in public subnets (dev access via public IP)

No NAT Gateway = Cost savings (~$32/month)

Subnet Layout:
--------------
- Public Subnet 1: 10.0.1.0/24 (AZ 1)
- Public Subnet 2: 10.0.2.0/24 (AZ 2)

Multi-AZ Design:
----------------
- Two subnets across different Availability Zones
- Required for RDS high availability (subnet group needs 2+ AZs)
"""

import pulumi
import pulumi_aws as aws


class SimpleVpc(pulumi.ComponentResource):
    """VPC with public subnets only (no NAT Gateway)."""

    def __init__(self, name: str, opts: pulumi.ResourceOptions = None):
        super().__init__("custom:network:SimpleVpc", name, None, opts)

        # =====================================================================
        # VPC - The network container
        # =====================================================================
        # CIDR 10.0.0.0/16 provides 65,536 IP addresses
        # DNS support required for RDS endpoint resolution
        self.vpc = aws.ec2.Vpc(
            f"{name}-vpc",
            cidr_block="10.0.0.0/16",
            enable_dns_hostnames=True,  # Required for RDS DNS names
            enable_dns_support=True,     # Required for internal DNS resolution
            tags={"Name": f"{name}-vpc"},
            opts=pulumi.ResourceOptions(parent=self),
        )

        # =====================================================================
        # Internet Gateway - Public internet access
        # =====================================================================
        # Attached to VPC, provides route to internet for public subnets
        self.igw = aws.ec2.InternetGateway(
            f"{name}-igw",
            vpc_id=self.vpc.id,
            tags={"Name": f"{name}-igw"},
            opts=pulumi.ResourceOptions(parent=self),
        )

        # =====================================================================
        # Availability Zones - Multi-AZ for reliability
        # =====================================================================
        # Get first two AZs in the region (e.g., us-east-2a, us-east-2b)
        azs = aws.get_availability_zones(state="available")
        az1 = azs.names[0]
        az2 = azs.names[1]

        # =====================================================================
        # Public Subnets - Direct internet access
        # =====================================================================
        self.public_subnet_1 = aws.ec2.Subnet(
            f"{name}-public-1",
            vpc_id=self.vpc.id,
            cidr_block="10.0.1.0/24",  # 256 IPs
            availability_zone=az1,
            map_public_ip_on_launch=True,  # Auto-assign public IPs
            tags={"Name": f"{name}-public-1"},
            opts=pulumi.ResourceOptions(parent=self),
        )

        self.public_subnet_2 = aws.ec2.Subnet(
            f"{name}-public-2",
            vpc_id=self.vpc.id,
            cidr_block="10.0.2.0/24",
            availability_zone=az2,
            map_public_ip_on_launch=True,
            tags={"Name": f"{name}-public-2"},
            opts=pulumi.ResourceOptions(parent=self),
        )

        # =====================================================================
        # Route Table - Traffic routing rules
        # =====================================================================
        # Public route table: 0.0.0.0/0 → Internet Gateway
        self.public_rt = aws.ec2.RouteTable(
            f"{name}-public-rt",
            vpc_id=self.vpc.id,
            routes=[
                aws.ec2.RouteTableRouteArgs(
                    cidr_block="0.0.0.0/0",
                    gateway_id=self.igw.id,  # Direct to internet
                )
            ],
            tags={"Name": f"{name}-public-rt"},
            opts=pulumi.ResourceOptions(parent=self),
        )

        # =====================================================================
        # Route Table Associations - Connect subnets to route table
        # =====================================================================
        aws.ec2.RouteTableAssociation(
            f"{name}-public-1-rta",
            subnet_id=self.public_subnet_1.id,
            route_table_id=self.public_rt.id,
            opts=pulumi.ResourceOptions(parent=self),
        )
        aws.ec2.RouteTableAssociation(
            f"{name}-public-2-rta",
            subnet_id=self.public_subnet_2.id,
            route_table_id=self.public_rt.id,
            opts=pulumi.ResourceOptions(parent=self),
        )

        # =====================================================================
        # Outputs - Subnet ID lists for other components
        # =====================================================================
        self.public_subnet_ids = [self.public_subnet_1.id, self.public_subnet_2.id]

        self.register_outputs(
            {
                "vpc_id": self.vpc.id,
                "public_subnet_ids": self.public_subnet_ids,
            }
        )
