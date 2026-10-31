terraform {
  required_version = ">= 1.7, < 2.0"
  required_providers {
    sumologic = {
      source  = "SumoLogic/sumologic"
      version = "~> 3.0"
    }
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

# Credentials come from SUMOLOGIC_ACCESSID / SUMOLOGIC_ACCESSKEY and the
# normal AWS credential chain (for example AWS_PROFILE), never from tfvars.
provider "sumologic" {
  environment = var.sumo_deployment
}

provider "aws" {
  region = var.aws_region
}

variable "sumo_deployment" {
  description = "Sumo API deployment, e.g. us2; unrelated to application environment."
  type        = string
}

variable "aws_region" {
  description = "AWS region in which ECS will read its Secrets Manager references."
  type        = string
}

variable "environments" {
  description = "Stable ingestion boundaries. One Hosted Collector and HTTP Source per entry."
  type        = set(string)
  default     = ["sandbox", "prod"]
  validation {
    condition     = length(var.environments) > 0 && alltrue([for env in var.environments : can(regex("^[a-z0-9]+(-[a-z0-9]+)*$", env)) && length(env) <= 63])
    error_message = "Use nonempty lowercase, hyphen-separated environment names of at most 63 characters."
  }
}

resource "sumologic_collector" "ecs" {
  for_each    = var.environments
  name        = "ecs-${each.key}"
  description = "Shared ${each.key} ECS application logs; not tied to a task or deployment."
}

resource "sumologic_http_source" "logs" {
  for_each                     = var.environments
  name                         = "ecs-application-logs"
  collector_id                 = sumologic_collector.ecs[each.key].id
  description                  = "FireLens JSON lines; X-Sumo-Category supplies application/component."
  category                     = "ecs/${each.key}/unclassified/unknown"
  message_per_request          = false
  multiline_processing_enabled = false
  use_autoline_matching        = false
}

locals {
  # Keep the token-bearing path in Secrets Manager, out of task definitions.
  # This example uses the URL-token form of the HTTP Logs Source endpoint.
  endpoint_parts = {
    for env, source in sumologic_http_source.logs : env => regex("^https://([^/]+)(/receiver/v1/http/[^?#]+)$", source.url)
  }
}

resource "aws_secretsmanager_secret" "sumo_uri" {
  for_each                = var.environments
  name                    = "ecs/${each.key}/sumo-uri"
  description             = "Sensitive HTTP Source URI for ecs-${each.key} FireLens output."
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "sumo_uri" {
  for_each      = var.environments
  secret_id     = aws_secretsmanager_secret.sumo_uri[each.key].id
  secret_string = local.endpoint_parts[each.key][1]
}

output "destinations" {
  description = "Non-secret renderer input: hostname and ARN only."
  value = {
    for env in var.environments : env => {
      # Only the public hostname is declassified; never the URL/path/token.
      host           = nonsensitive(sensitive(local.endpoint_parts[env][0]))
      uri_secret_arn = aws_secretsmanager_secret.sumo_uri[env].arn
    }
  }
}

output "source_urls" {
  description = "Sensitive full URLs for integration with other deployment tooling. Do not commit or print in CI."
  value       = { for env, source in sumologic_http_source.logs : env => source.url }
  sensitive   = true
}
