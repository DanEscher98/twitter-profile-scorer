import pulumi
import pulumi_aws as aws


class Vpc(pulumi.ComponentResource):
    """VPC with public, private, and isolated subnets"""

    def __init__(self, name: str, opts: pulumi.ResourceOptions = None):
        super().__init__("custom:network:Vpc", name, None, opts)

        # VPC
        self.vpc = aws.ec2.Vpc(
            f"{name}-vpc",
            cidr_block="10.0.0.0/16",
            enable_dns_hostnames=True,
            enable_dns_support=True,
            tags={"Name": f"{name}-vpc"},
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Internet Gateway
        self.igw = aws.ec2.InternetGateway(
            f"{name}-igw",
            vpc_id=self.vpc.id,
            tags={"Name": f"{name}-igw"},
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Get AZs
        azs = aws.get_availability_zones(state="available")
        az1 = azs.names[0]
        az2 = azs.names[1]

        # Public subnets (for NAT Gateway)
        self.public_subnet_1 = aws.ec2.Subnet(
            f"{name}-public-1",
            vpc_id=self.vpc.id,
            cidr_block="10.0.1.0/24",
            availability_zone=az1,
            map_public_ip_on_launch=True,
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

        # Private subnets (Lambda with internet via NAT)
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

        # Isolated subnets (DB only, no internet)
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

        # Elastic IP for NAT
        self.nat_eip = aws.ec2.Eip(
            f"{name}-nat-eip",
            domain="vpc",
            tags={"Name": f"{name}-nat-eip"},
            opts=pulumi.ResourceOptions(parent=self),
        )

        # NAT Gateway (single for cost savings)
        self.nat_gateway = aws.ec2.NatGateway(
            f"{name}-nat",
            allocation_id=self.nat_eip.id,
            subnet_id=self.public_subnet_1.id,
            tags={"Name": f"{name}-nat"},
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Route tables
        self.public_rt = aws.ec2.RouteTable(
            f"{name}-public-rt",
            vpc_id=self.vpc.id,
            routes=[
                aws.ec2.RouteTableRouteArgs(
                    cidr_block="0.0.0.0/0",
                    gateway_id=self.igw.id,
                )
            ],
            tags={"Name": f"{name}-public-rt"},
            opts=pulumi.ResourceOptions(parent=self),
        )

        self.private_rt = aws.ec2.RouteTable(
            f"{name}-private-rt",
            vpc_id=self.vpc.id,
            routes=[
                aws.ec2.RouteTableRouteArgs(
                    cidr_block="0.0.0.0/0",
                    nat_gateway_id=self.nat_gateway.id,
                )
            ],
            tags={"Name": f"{name}-private-rt"},
            opts=pulumi.ResourceOptions(parent=self),
        )

        self.isolated_rt = aws.ec2.RouteTable(
            f"{name}-isolated-rt",
            vpc_id=self.vpc.id,
            tags={"Name": f"{name}-isolated-rt"},
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Route table associations
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

        # Expose outputs
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
