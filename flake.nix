{
  description = "Hermes Web UI";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs, ... }:
    let
      supportedSystems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];
      linuxSystems = [ "x86_64-linux" "aarch64-linux" ];
      forAllSystems = nixpkgs.lib.genAttrs supportedSystems;
      hermesModule = import ./nix/nixosModules.nix { inherit self; };
      perSystem = forAllSystems (system: let
        pkgs = import nixpkgs { inherit system; };
        package = import ./nix/packages.nix {
          inherit pkgs;
          version = "0.51.0";
        };
        moduleChecks = if builtins.elem system linuxSystems then
          let
            moduleConfig = nixpkgs.lib.nixosSystem {
              inherit system;
              modules = [
                hermesModule
                {
                  services.hermes-webui = {
                    enable = true;
                    package = package;
                    host = "0.0.0.0";
                    port = 8787;
                    stateDir = "/var/lib/hermes-webui";
                    agent.dir = "/var/lib/hermes-agent";
                  };
                }
              ];
            };
            moduleServiceEnvironment = nixpkgs.lib.concatStringsSep "\n" moduleConfig.config.systemd.services.hermes-webui.serviceConfig.Environment;
            envProbe = pkgs.writeText "hermes-webui-nixos-env-${system}.txt" moduleServiceEnvironment;
          in
          {
            module-env-mapping = pkgs.runCommand "hermes-webui-nixos-module-${system}" {
              nativeBuildInputs = [ pkgs.coreutils ];
            } ''
              grep -q 'HERMES_WEBUI_HOST=0.0.0.0' ${envProbe}
              grep -q 'HERMES_WEBUI_PORT=8787' ${envProbe}
              grep -q 'HERMES_WEBUI_STATE_DIR=/var/lib/hermes-webui' ${envProbe}
              grep -q 'HERMES_WEBUI_AGENT_DIR=/var/lib/hermes-agent' ${envProbe}
              touch "$out"
            '';
          }
        else
          { };
      in
      {
        packages = {
          hermes-webui = package;
          default = package;
        };

        apps = {
          default = {
            type = "app";
            program = "${package}/bin/hermes-webui";
          };
        };

        checks = moduleChecks // {
          package = package;
        };
      });
    in
    {
      packages = forAllSystems (system: perSystem.${system}.packages);
      apps = forAllSystems (system: perSystem.${system}.apps);
      checks = nixpkgs.lib.genAttrs linuxSystems (system: perSystem.${system}.checks);

      nixosModules = {
        default = hermesModule;
        hermes-webui = hermesModule;
      };
    };
}
