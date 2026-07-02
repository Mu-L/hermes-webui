{ pkgs, version ? "0.51.0" }:

let
  pythonEnv = pkgs.python3.withPackages (
    ps: with ps; [
      pyyaml
      cryptography
    ]
  );

  runtimeDir = "hermes-webui";
in
pkgs.stdenv.mkDerivation {
  pname = "hermes-webui";
  inherit version;

  dontUnpack = true;
  dontBuild = true;
  nativeBuildInputs = [ pkgs.makeWrapper ];

  installPhase = ''
    runHook preInstall

    mkdir -p "$out/${runtimeDir}" "$out/bin"

    cp "${./../bootstrap.py}" "$out/${runtimeDir}/bootstrap.py"
    cp "${./../server.py}" "$out/${runtimeDir}/server.py"
    cp "${./../mcp_server.py}" "$out/${runtimeDir}/mcp_server.py"
    cp "${./../requirements.txt}" "$out/${runtimeDir}/requirements.txt"
    cp -r "${./../api}" "$out/${runtimeDir}/"
    cp -r "${./../static}" "$out/${runtimeDir}/"

    makeWrapper ${pythonEnv}/bin/python3 "$out/bin/hermes-webui" \
      --add-flags "$out/${runtimeDir}/bootstrap.py --foreground --no-browser"

    runHook postInstall
  '';

  meta = {
    description = "Hermes WebUI package";
    mainProgram = "hermes-webui";
  };
}
