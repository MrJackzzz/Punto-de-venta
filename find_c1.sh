docker inspect cliente1-web-1 --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}'
docker inspect cliente1-web-1 --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}'
docker inspect cliente1-db-1 --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}'
