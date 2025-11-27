"""
VPC Component - Network Foundation

This module creates a VPC with three subnet tiers for security isolation:

Subnet Tiers:
-------------
1. Public Subnets (10.0.1.0/24, 10.0.2.0/24)
   - Direct internet access via Internet Gateway
   - Used for: NAT Gateway, RDS (dev only)
   - map_public_ip_on_launch=True for direct connectivity

2. Private Subnets (10.0.10.0/24, 10.0.11.0/24)
   - Internet access via NAT Gateway (outbound only)
   - Used for: Lambdas that call external APIs (RapidAPI, Claude)
   - No inbound internet access = reduced attack surface

3. Isolated Subnets (10.0.20.0/24, 10.0.21.0/24)
   - NO internet access at all
   - Used for: Lambdas that only need DB access (keyword-engine)
   - Maximum security - no network path to/from internet

Why Three Tiers?
----------------
- Defense in depth: Compromised Lambda can't reach internet
- Cost optimization: Only pay for NAT traffic when needed
- Compliance: PCI/HIPAA often require isolated database subnets

Multi-AZ Design:
----------------
- Two subnets per tier across different Availability Zones
- Required for RDS high availability (subnet group needs 2+ AZs)
- NAT Gateway is single-AZ for cost (add second for production HA)
"""

import pulumi
import pulumi_aws as aws


class Vpc(pulumi.ComponentResource):
    """VPC with public, private, and isolated subnets across two AZs."""

    def __init__(self, name: str, opts: pulumi.ResourceOptions = None):
        super().__init__("custom:network:Vpc", name, None, opts)

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
        # Used for NAT Gateway and RDS (dev only for external access)
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
        # Private Subnets - Internet via NAT (outbound only)
        # =====================================================================
        # Used for Lambdas needing external API access (RapidAPI, Claude, AWS APIs)
        self.private_subnet_1 = aws.ec2.Subnet(
            f"{name}-private-1",
            vpc_id=self.vpc.id,
            cidr_block="10.0.10.0/24",
            availability_zone=az1,
            tags={"Name": f"{name}-private-1"},
            opts=pulumi.ResourceOptions(parent=self),
        )

        self.private_subnet_2 = aws.ec2.Subnet(
            f"{name}-private-2",
            vpc_id=self.vpc.id,
            cidr_block="10.0.11.0/24",
            availability_zone=az2,
            tags={"Name": f"{name}-private-2"},
            opts=pulumi.ResourceOptions(parent=self),
        )

        # =====================================================================
        # Isolated Subnets - No internet access
        # =====================================================================
        # Used for DB-only Lambdas (keyword-engine) - maximum security
        self.isolated_subnet_1 = aws.ec2.Subnet(
            f"{name}-isolated-1",
            vpc_id=self.vpc.id,
            cidr_block="10.0.20.0/24",
            availability_zone=az1,
            tags={"Name": f"{name}-isolated-1"},
            opts=pulumi.ResourceOptions(parent=self),
        )

        self.isolated_subnet_2 = aws.ec2.Subnet(
            f"{name}-isolated-2",
            vpc_id=self.vpc.id,
            cidr_block="10.0.21.0/24",
            availability_zone=az2,
            tags={"Name": f"{name}-isolated-2"},
            opts=pulumi.ResourceOptions(parent=self),
        )

        # =====================================================================
        # NAT Gateway - Outbound internet for private subnets
        # =====================================================================
        # Elastic IP is required for NAT Gateway (static outbound IP)
        self.nat_eip = aws.ec2.Eip(
            f"{name}-nat-eip",
            domain="vpc",
            tags={"Name": f"{name}-nat-eip"},
            opts=pulumi.ResourceOptions(parent=self),
        )

        # NAT Gateway must be in public subnet (needs internet access itself)
        # Single NAT for cost savings - add second in public-2 for production HA
        self.nat_gateway = aws.ec2.NatGateway(
            f"{name}-nat",
            allocation_id=self.nat_eip.id,
            subnet_id=self.public_subnet_1.id,
            tags={"Name": f"{name}-nat"},
            opts=pulumi.ResourceOptions(parent=self),
        )

        # =====================================================================
        # Route Tables - Traffic routing rules
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

        # Private route table: 0.0.0.0/0 → NAT Gateway
        self.private_rt = aws.ec2.RouteTable(
            f"{name}-private-rt",
            vpc_id=self.vpc.id,
            routes=[
                aws.ec2.RouteTableRouteArgs(
                    cidr_block="0.0.0.0/0",
                    nat_gateway_id=self.nat_gateway.id,  # Via NAT
                )
            ],
            tags={"Name": f"{name}-private-rt"},
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Isolated route table: NO routes to internet (VPC-local only)
        self.isolated_rt = aws.ec2.RouteTable(
            f"{name}-isolated-rt",
            vpc_id=self.vpc.id,
            # No routes = no internet access, only VPC-local traffic
            tags={"Name": f"{name}-isolated-rt"},
            opts=pulumi.ResourceOptions(parent=self),
        )

        # =====================================================================
        # Route Table Associations - Connect subnets to route tables
        # =====================================================================

        # Public subnets → public route table
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

        # Private subnets → private route table (NAT)
        aws.ec2.RouteTableAssociation(
            f"{name}-private-1-rta",
            subnet_id=self.private_subnet_1.id,
            route_table_id=self.private_rt.id,
            opts=pulumi.ResourceOptions(parent=self),
        )
        aws.ec2.RouteTableAssociation(
            f"{name}-private-2-rta",
            subnet_id=self.private_subnet_2.id,
            route_table_id=self.private_rt.id,
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Isolated subnets → isolated route table (no internet)
        aws.ec2.RouteTableAssociation(
            f"{name}-isolated-1-rta",
            subnet_id=self.isolated_subnet_1.id,
            route_table_id=self.isolated_rt.id,
            opts=pulumi.ResourceOptions(parent=self),
        )
        aws.ec2.RouteTableAssociation(
            f"{name}-isolated-2-rta",
            subnet_id=self.isolated_subnet_2.id,
            route_table_id=self.isolated_rt.id,
            opts=pulumi.ResourceOptions(parent=self),
        )

        # =====================================================================
        # Outputs - Subnet ID lists for other components
        # =====================================================================
        self.public_subnet_ids = [self.public_subnet_1.id, self.public_subnet_2.id]
        self.private_subnet_ids = [self.private_subnet_1.id, self.private_subnet_2.id]
        self.isolated_subnet_ids = [
            self.isolated_subnet_1.id,
            self.isolated_subnet_2.id,
        ]

        self.register_outputs(
            {
                "vpc_id": self.vpc.id,
                "public_subnet_ids": self.public_subnet_ids,
                "private_subnet_ids": self.private_subnet_ids,
                "isolated_subnet_ids": self.isolated_subnet_ids,
            }
        )
