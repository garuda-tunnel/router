# Changelog

## [0.4.2](https://github.com/garuda-tunnel/router-internal/compare/v0.4.1...v0.4.2) (2026-06-17)


### Bug Fixes

* **ipt-server:** use 127.0.0.1 in hook.lua, remove hostAliases ([dd73fd1](https://github.com/garuda-tunnel/router-internal/commit/dd73fd1fd25497bea63e52d1c78b47a53f5419e9))

## [0.4.1](https://github.com/garuda-tunnel/router-internal/compare/v0.4.0...v0.4.1) (2026-06-17)


### Bug Fixes

* **ipt-server:** add hostAliases for garuda_ipt WebSocket backend ([d22f404](https://github.com/garuda-tunnel/router-internal/commit/d22f404b82554dbcc8c0bb08d600de9138955de3))
* **ipt-server:** add hostAliases for garuda_ipt WebSocket backend (DNS interception) ([dedb9a2](https://github.com/garuda-tunnel/router-internal/commit/dedb9a250ddf240cb0691b4cfee54fcc81b2e0d4))

## [0.4.0](https://github.com/garuda-tunnel/router-internal/compare/v0.3.0...v0.4.0) (2026-06-17)


### Features

* **ipt-server:** emit app.kubernetes.io/part-of=garuda pod label ([2169f2b](https://github.com/garuda-tunnel/router-internal/commit/2169f2b25c28c546abcd2bb51904f85b83ea2a01))
* **ipt-server:** emit app.kubernetes.io/part-of=garuda pod label ([255733a](https://github.com/garuda-tunnel/router-internal/commit/255733a8d491d837d4252c6f74b0ede07b978ead))

## [0.3.0](https://github.com/garuda-tunnel/router-internal/compare/v0.2.0...v0.3.0) (2026-06-16)


### Features

* **chart:** bump frr-sidecar dependency 0.1.0 -&gt; 0.2.0 ([3e82a63](https://github.com/garuda-tunnel/router-internal/commit/3e82a634aae12b370ae4c2375d3c59e5674cca18))
* **chart:** bump frr-sidecar dependency 0.1.0 → 0.2.0 ([17f0473](https://github.com/garuda-tunnel/router-internal/commit/17f047392fa9ae5330555c27b26afd2745bda92b))

## [0.2.0](https://github.com/garuda-tunnel/router-internal/compare/v0.1.0...v0.2.0) (2026-06-16)


### Features

* **kube:** consume ipt-server chart from OCI; move checksum to Helm-native ([ccd8475](https://github.com/garuda-tunnel/router-internal/commit/ccd84752e2c74d7cac2a29c195a9680c43251d39))
* **kube:** Sub-project B — consume ipt-server chart from OCI; move checksum to Helm-native ([967dcee](https://github.com/garuda-tunnel/router-internal/commit/967dcee8e0b5de72c7f22890f191ac797fb689f6))
* pin ipt-server+powerdns digests (Phase 1) ([945c4ce](https://github.com/garuda-tunnel/router-internal/commit/945c4ce0775e2f8073f47779c438612a84a095f1))
* pin ipt-server+powerdns digests; TF conditional override; serialize powerdns job (needs, no chart_path); caller inputs; regression tests ([bb88dd5](https://github.com/garuda-tunnel/router-internal/commit/bb88dd5d614c7e17834c0fb156cc931df7157e0b))
* router tag-model publish — two images, one chart (sub-project A) ([268ea62](https://github.com/garuda-tunnel/router-internal/commit/268ea624f3021321bf482cb48a6b0865cd17bc33))
* router tag-model publish (two images, chart needs both via skip_image; dev-image; fallbacks) ([fb25443](https://github.com/garuda-tunnel/router-internal/commit/fb254432c87371d815a8d4c66c9567d95e21a322))
