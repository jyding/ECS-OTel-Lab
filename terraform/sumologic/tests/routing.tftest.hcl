# Mock providers never contact Sumo or AWS and never provision infrastructure.
mock_provider "sumologic" {
  mock_resource "sumologic_collector" {
    defaults = { id = "123" }
  }
}
mock_provider "aws" {}

variables {
  sumo_deployment = "us2"
  aws_region      = "us-east-1"
}

override_resource {
  target = sumologic_http_source.logs["sandbox"]
  values = { url = "https://sandbox.example.invalid/receiver/v1/http/SANDBOX_EXAMPLE" }
}
override_resource {
  target = sumologic_http_source.logs["prod"]
  values = { url = "https://prod.example.invalid/receiver/v1/http/PROD_EXAMPLE" }
}

run "environment_routing" {
  command = apply

  assert {
    condition = alltrue([
      for env in ["sandbox", "prod"] :
      sumologic_collector.ecs[env].name == "ecs-${env}" &&
      sumologic_http_source.logs[env].collector_id == tonumber(sumologic_collector.ecs[env].id) &&
      sumologic_http_source.logs[env].category == "ecs/${env}/unclassified/unknown" &&
      !sumologic_http_source.logs[env].multiline_processing_enabled &&
      !sumologic_http_source.logs[env].message_per_request
    ])
    error_message = "Each environment needs its own stable Collector and a JSON-lines Source."
  }

  assert {
    condition = alltrue([
      for env in ["sandbox", "prod"] :
      output.destinations[env].host == "${env}.example.invalid" &&
      output.destinations[env].uri_secret_arn == aws_secretsmanager_secret.sumo_uri[env].arn &&
      aws_secretsmanager_secret.sumo_uri[env].name == "ecs/${env}/sumo-uri" &&
      aws_secretsmanager_secret_version.sumo_uri[env].secret_string == "/receiver/v1/http/${upper(env)}_EXAMPLE"
    ])
    error_message = "The public hostname and secret URI must come from the same environment's Source."
  }
}
