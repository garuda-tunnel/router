# Changelog

## [1.2.2](https://github.com/garuda-tunnel/router-internal/compare/v1.2.1...v1.2.2) (2026-07-01)


### Bug Fixes

* **ipt-server:** bump appVersion for background-task retention fix ([#27](https://github.com/garuda-tunnel/router-internal/issues/27)) ([bbb53d5](https://github.com/garuda-tunnel/router-internal/commit/bbb53d5121a834018ecfdc75f4c76cca59d20b01))

## [1.2.1](https://github.com/garuda-tunnel/router-internal/compare/v1.2.0...v1.2.1) (2026-07-01)


### Bug Fixes

* **ipt-server:** bump appVersion for egress-nexthop resolution fix ([b64ea2b](https://github.com/garuda-tunnel/router-internal/commit/b64ea2ba1608d89f7e2cb37b4d786fdacd3181e9))
* **ipt-server:** bump appVersion for egress-nexthop resolution fix ([a1cdbb5](https://github.com/garuda-tunnel/router-internal/commit/a1cdbb529fbcbc8cb79f5a45916d3854c16f9325))

## [1.2.0](https://github.com/garuda-tunnel/router-internal/compare/v1.1.0...v1.2.0) (2026-06-24)


### Features

* **ipt-server:** vanilla guest chart — podLabels/podAnnotations, drop frr-sidecar dep + networks helper + provider preamble ([f67e645](https://github.com/garuda-tunnel/router-internal/commit/f67e64564c95850b6573bd7895b9970b9e5671f6))
* vanilla-guest passthrough + drop frr-sidecar (Phase 4+5) ([496c2fd](https://github.com/garuda-tunnel/router-internal/commit/496c2fdfed426396fe31bfe3ae448c3ee7da4cad))

## [1.1.0](https://github.com/garuda-tunnel/router-internal/compare/v1.0.0...v1.1.0) (2026-06-19)


### Features

* **border-router:** unified local egress; ipt_server becomes backbone-only ([#58](https://github.com/garuda-tunnel/router-internal/issues/58)) ([cecfe82](https://github.com/garuda-tunnel/router-internal/commit/cecfe8283eee983ea3b66d633a4c96fc32daf647))
* **chart:** bump frr-sidecar dependency 0.1.0 -&gt; 0.2.0 ([3e82a63](https://github.com/garuda-tunnel/router-internal/commit/3e82a634aae12b370ae4c2375d3c59e5674cca18))
* **chart:** bump frr-sidecar dependency 0.1.0 → 0.2.0 ([17f0473](https://github.com/garuda-tunnel/router-internal/commit/17f047392fa9ae5330555c27b26afd2745bda92b))
* **ipt_server:** consume frr-sidecar via OCI; drop frr template checksum inputs ([51e6c71](https://github.com/garuda-tunnel/router-internal/commit/51e6c71b47fc47b68b0f3735e859995066d2fded))
* **ipt-server:** central forward MSS clamp (MTU/MSS Task 4) ([7cff960](https://github.com/garuda-tunnel/router-internal/commit/7cff96044c7fb7db3cc0c2828a91535fb3523bca))
* **ipt-server:** central forward MSS clamp (separate inet table) ([e607d94](https://github.com/garuda-tunnel/router-internal/commit/e607d945e0cbc89e7f38634820d15430245dfb6b))
* **ipt-server:** emit app.kubernetes.io/part-of=garuda pod label ([2169f2b](https://github.com/garuda-tunnel/router-internal/commit/2169f2b25c28c546abcd2bb51904f85b83ea2a01))
* **ipt-server:** emit app.kubernetes.io/part-of=garuda pod label ([255733a](https://github.com/garuda-tunnel/router-internal/commit/255733a8d491d837d4252c6f74b0ede07b978ead))
* **kube:** consume ipt-server chart from OCI; move checksum to Helm-native ([ccd8475](https://github.com/garuda-tunnel/router-internal/commit/ccd84752e2c74d7cac2a29c195a9680c43251d39))
* **kube:** Sub-project B — consume ipt-server chart from OCI; move checksum to Helm-native ([967dcee](https://github.com/garuda-tunnel/router-internal/commit/967dcee8e0b5de72c7f22890f191ac797fb689f6))
* normalize ipt-server mss policy ([1038af1](https://github.com/garuda-tunnel/router-internal/commit/1038af1715a3a2a48987c1fdf5f70ff39484afa9))
* pin ipt-server+powerdns digests (Phase 1) ([945c4ce](https://github.com/garuda-tunnel/router-internal/commit/945c4ce0775e2f8073f47779c438612a84a095f1))
* pin ipt-server+powerdns digests; TF conditional override; serialize powerdns job (needs, no chart_path); caller inputs; regression tests ([bb88dd5](https://github.com/garuda-tunnel/router-internal/commit/bb88dd5d614c7e17834c0fb156cc931df7157e0b))
* router tag-model publish — two images, one chart (sub-project A) ([268ea62](https://github.com/garuda-tunnel/router-internal/commit/268ea624f3021321bf482cb48a6b0865cd17bc33))
* router tag-model publish (two images, chart needs both via skip_image; dev-image; fallbacks) ([fb25443](https://github.com/garuda-tunnel/router-internal/commit/fb254432c87371d815a8d4c66c9567d95e21a322))
* unify MTU/MSS policy — ipt-server mss + chart 1.0.0 ([8d2bcab](https://github.com/garuda-tunnel/router-internal/commit/8d2bcab5a09267f980784dabc394f6dbe98e762d))


### Bug Fixes

* **hub-k3s-cutover:** tag-correct transit provider + watcher fallback + smoke green ([#47](https://github.com/garuda-tunnel/router-internal/issues/47)) ([3c42b26](https://github.com/garuda-tunnel/router-internal/commit/3c42b2623ef4aa3e65a3a4c8277f68f1ca17b6aa))
* **hub-k3s-cutover:** tag-correct transit provider + watcher fallback + smoke green ([#47](https://github.com/garuda-tunnel/router-internal/issues/47)) ([3c42b26](https://github.com/garuda-tunnel/router-internal/commit/3c42b2623ef4aa3e65a3a4c8277f68f1ca17b6aa))
* **ipt-server:** add hostAliases for garuda_ipt WebSocket backend ([d22f404](https://github.com/garuda-tunnel/router-internal/commit/d22f404b82554dbcc8c0bb08d600de9138955de3))
* **ipt-server:** add hostAliases for garuda_ipt WebSocket backend (DNS interception) ([dedb9a2](https://github.com/garuda-tunnel/router-internal/commit/dedb9a250ddf240cb0691b4cfee54fcc81b2e0d4))
* **ipt-server:** align naming per spec (chain mss_clamp, iptServer.mssClampValue) ([5bfa0e3](https://github.com/garuda-tunnel/router-internal/commit/5bfa0e3bf62bf8b7072ecf62ec2777a541a61f4c))
* **ipt-server:** use 127.0.0.1 in hook.lua, remove hostAliases ([dd73fd1](https://github.com/garuda-tunnel/router-internal/commit/dd73fd1fd25497bea63e52d1c78b47a53f5419e9))

## [0.5.0](https://github.com/garuda-tunnel/router-internal/compare/v0.4.2...v0.5.0) (2026-06-18)


### Features

* **ipt-server:** central forward MSS clamp (MTU/MSS Task 4) ([7cff960](https://github.com/garuda-tunnel/router-internal/commit/7cff96044c7fb7db3cc0c2828a91535fb3523bca))
* **ipt-server:** central forward MSS clamp (separate inet table) ([e607d94](https://github.com/garuda-tunnel/router-internal/commit/e607d945e0cbc89e7f38634820d15430245dfb6b))


### Bug Fixes

* **ipt-server:** align naming per spec (chain mss_clamp, iptServer.mssClampValue) ([5bfa0e3](https://github.com/garuda-tunnel/router-internal/commit/5bfa0e3bf62bf8b7072ecf62ec2777a541a61f4c))

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
