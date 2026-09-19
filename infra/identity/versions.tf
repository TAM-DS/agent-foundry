terraform {
  required_version = ">= 1.16, < 2.0"

  backend "s3" {
    bucket       = "agent-foundry-terraform-state-276713393004-us-east-1"
    key          = "identity/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}
