{ lib, stdenv, fetchFromGitHub, fetchurl, kernel }:
assert lib.versions.majorMinor kernel.version == "6.12";
stdenv.mkDerivation {
  pname = "rknpu-mainline";
  version = "0.9.8-a8792fe-${kernel.version}";
  src = fetchFromGitHub {
    owner = "w568w";
    repo = "rknpu-module";
    rev = "a8792fe6b633f90cf2c6808267cac327537ab4cf";
    sha256 = "1fas42f572mj3zqn4n3gp2jj87r6rmqg294wgfs7rsaisvi6r4g2";
  };
  patches = [ ./linux-6.12.patch ];
  # Linux keeps this governor interface under drivers/, outside the headers
  # exported for external modules. Use the matching 6.12 definition.
  governorHeader = fetchurl {
    url = "https://raw.githubusercontent.com/torvalds/linux/v6.12/drivers/devfreq/governor.h";
    sha256 = "c7cbcd63ac874d2e2c9237ffc06e0384d54f1ce5d40c8184472ae4a93dfcf2e1";
  };
  postPatch = ''
    mkdir -p src/include/compat/linux
    cp "$governorHeader" src/include/compat/linux/devfreq-governor.h
  '';
  nativeBuildInputs = kernel.moduleBuildDependencies;
  hardeningDisable = [ "pic" ];
  makeFlags = [
    "KDIR=${kernel.dev}/lib/modules/${kernel.modDirVersion}/build"
    "ARCH=arm64"
    "CROSS_COMPILE=${stdenv.cc.targetPrefix}"
  ];
  installPhase = ''
    runHook preInstall
    mkdir -p $out/lib/modules/${kernel.modDirVersion}/extra
    cp rknpu.ko $out/lib/modules/${kernel.modDirVersion}/extra/
    runHook postInstall
  '';
  meta = {
    description = "Experimental RK3566 NPU driver for the appliance's Linux 6.12 kernel";
    homepage = "https://github.com/w568w/rknpu-module";
    license = lib.licenses.gpl2Only;
    platforms = [ "aarch64-linux" ];
  };
}
