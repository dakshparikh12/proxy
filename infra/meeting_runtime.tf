resource "google_compute_instance_group_manager" "meeting_runtime" {
  name               = "meeting-runtime"
  base_instance_name = "meeting-runtime"
  zone               = "us-central1-a"
  target_size        = 1

  version {
    instance_template = google_compute_instance_template.meeting_runtime.id
  }

  named_port {
    name = "harness"
    port = 8080
  }
}

# One asyncio harness process per meeting; state is the Postgres substrate, so
# the template holds only an ephemeral boot image and no persistent data.
resource "google_compute_instance_template" "meeting_runtime" {
  name         = "meeting-runtime-tpl"
  machine_type = "e2-standard-2"
  region       = var.region

  disk {
    source_image = "projects/debian-cloud/global/images/family/debian-12"
    auto_delete  = true
    boot         = true
  }

  network_interface {
    network = google_compute_network.core.id
  }
}
