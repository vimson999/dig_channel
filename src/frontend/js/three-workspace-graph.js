(function () {
    const COLORS = {
        group: '#2563eb',
        tab: '#0ea5e9',
        topic: '#172033',
        attachment: '#10b981',
        external: '#f59e0b',
        summary: '#8b5cf6',
        audio: '#ec4899',
        video: '#ef4444',
        stage: '#64748b',
        run: '#14b8a6',
        worker: '#6366f1',
        failed: '#ef4444',
        warning: '#f59e0b',
        success: '#10b981',
        idle: '#94a3b8',
    };

    const TYPE_LABELS = {
        group: '星球',
        tab: 'Tab',
        topic: '主题',
        attachment: '附件',
        external: '外链',
        summary: '总结',
        audio: '音频',
        video: '视频',
        stage: '阶段',
        run: '批次',
        worker: 'Worker',
    };

    function clamp(value, min, max) {
        return Math.max(min, Math.min(max, value));
    }

    function truncate(value, length = 30) {
        const text = String(value || '').replace(/\s+/g, ' ').trim();
        return text.length > length ? `${text.slice(0, length - 1)}…` : text;
    }

    function nodeColor(node) {
        if (node.color) return node.color;
        if (node.status && COLORS[node.status]) return COLORS[node.status];
        return COLORS[node.type] || COLORS.stage;
    }

    function normalizedModel(model) {
        const nodes = Array.isArray(model?.nodes) ? model.nodes.filter(Boolean) : [];
        const nodeIds = new Set(nodes.map(node => String(node.id)));
        const links = (Array.isArray(model?.links) ? model.links : [])
            .filter(link => nodeIds.has(String(link.source)) && nodeIds.has(String(link.target)));
        return {
            title: model?.title || '',
            subtitle: model?.subtitle || '',
            layout: model?.layout || 'radial',
            metrics: Array.isArray(model?.metrics) ? model.metrics.slice(0, 6) : [],
            legend: Array.isArray(model?.legend) ? model.legend : [],
            nodes,
            links,
        };
    }

    function nodeRank(node) {
        const explicitRank = Number(node?.rank ?? node?.layoutRank);
        return Number.isFinite(explicitRank) ? explicitRank : typeRank(node?.type);
    }

    function typeRank(type) {
        const ranks = {
            group: 0,
            stage: 0,
            worker: 0,
            tab: 1,
            run: 1,
            topic: 2,
            summary: 3,
            external: 3,
            attachment: 3,
            audio: 4,
            video: 5,
        };
        return ranks[type] ?? 2;
    }

    function computeColumnLayout(nodes, width, height) {
        const byRank = new Map();
        nodes.forEach(node => {
            const rank = nodeRank(node);
            if (!byRank.has(rank)) byRank.set(rank, []);
            byRank.get(rank).push(node);
        });

        const ranks = Array.from(byRank.keys()).sort((a, b) => a - b);
        const compact = width < 560;
        const worldWidth = compact
            ? clamp(width * 0.68, 300, 430)
            : clamp(width * 0.68, 580, 1080);
        const worldHeight = clamp(height * 0.92, 220, 340);
        const positions = new Map();
        const xStep = ranks.length > 1 ? worldWidth / (ranks.length - 1) : 0;
        const xStart = -worldWidth / 2;

        ranks.forEach((rank, rankIndex) => {
            const items = byRank.get(rank) || [];
            const xBase = ranks.length > 1 ? xStart + rankIndex * xStep : 0;
            const yStep = items.length > 1 ? worldHeight / (items.length - 1) : 0;
            items.forEach((node, index) => {
                const yBase = items.length > 1 ? worldHeight / 2 - index * yStep : 0;
                const stagger = items.length > 3 ? (index % 2 ? -14 : 14) : 0;
                const lastRankOffset = !compact && rankIndex === ranks.length - 1 ? 150 : 0;
                positions.set(node.id, {
                    x: xBase + stagger + lastRankOffset,
                    y: yBase,
                    z: (rankIndex % 2 ? -22 : 22) + Math.sin(index * 0.9) * 16,
                });
            });
        });

        return positions;
    }

    function computeLayout(nodes, width, height, layout = 'radial') {
        if (layout === 'columns') return computeColumnLayout(nodes, width, height);

        const byRank = new Map();
        nodes.forEach(node => {
            const rank = nodeRank(node);
            if (!byRank.has(rank)) byRank.set(rank, []);
            byRank.get(rank).push(node);
        });
        const ranks = Array.from(byRank.keys()).sort((a, b) => a - b);
        const maxRadius = Math.max(90, Math.min(width, height) * 0.34);
        const positions = new Map();

        ranks.forEach((rank, rankIndex) => {
            const items = byRank.get(rank);
            const radius = rankIndex === 0 ? 0 : maxRadius * (0.34 + rankIndex * 0.22);
            const zBase = (rankIndex % 2 ? -26 : 24) + rankIndex * 3;
            items.forEach((node, index) => {
                if (rankIndex === 0 && items.length === 1) {
                    positions.set(node.id, { x: 0, y: 0, z: 42 });
                    return;
                }
                const angle = ((Math.PI * 2) / Math.max(items.length, 1)) * index + rankIndex * 0.55;
                const spread = rankIndex === 0 ? 58 : radius;
                positions.set(node.id, {
                    x: Math.cos(angle) * spread,
                    y: Math.sin(angle) * spread * 0.68,
                    z: zBase + Math.sin(angle * 2.1) * 34,
                });
            });
        });
        return positions;
    }

    class WorkspaceGraph {
        constructor(root, options = {}) {
            this.root = typeof root === 'string' ? document.querySelector(root) : root;
            this.options = options || {};
            this.sceneEl = this.root?.querySelector('[data-three-scene]') || this.root;
            this.sideEl = this.root?.querySelector('[data-three-side]');
            this.labelLayer = null;
            this.labels = new Map();
            this.nodeMeshes = new Map();
            this.lineGroup = null;
            this.animationId = 0;
            this.model = normalizedModel({});
            this.pointer = { x: 0, y: 0 };
            this.rotationTarget = { x: -0.15, y: 0.35 };
            this.rotation = { x: -0.15, y: 0.35 };
            this.init();
        }

        init() {
            if (!this.root || !this.sceneEl) return;
            this.sceneEl.innerHTML = '';
            if (!window.THREE) {
                this.renderFallback();
                return;
            }
            const THREE = window.THREE;
            this.scene = new THREE.Scene();
            this.group = new THREE.Group();
            this.scene.add(this.group);
            this.camera = new THREE.PerspectiveCamera(42, 1, 1, 1800);
            this.camera.position.set(0, 0, 520);
            this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
            this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
            this.renderer.domElement.className = 'three-workspace-canvas';
            this.sceneEl.appendChild(this.renderer.domElement);
            this.labelLayer = document.createElement('div');
            this.labelLayer.className = 'three-workspace-label-layer';
            this.sceneEl.appendChild(this.labelLayer);
            this.lineGroup = new THREE.Group();
            this.group.add(this.lineGroup);
            this.scene.add(new THREE.AmbientLight(0xffffff, 1.6));
            const light = new THREE.DirectionalLight(0xffffff, 1.2);
            light.position.set(120, 180, 260);
            this.scene.add(light);
            this.sceneEl.addEventListener('pointermove', event => this.handlePointer(event));
            window.addEventListener('resize', () => this.resize());
            this.resize();
            this.animate();
        }

        handlePointer(event) {
            const box = this.sceneEl.getBoundingClientRect();
            this.pointer.x = ((event.clientX - box.left) / Math.max(box.width, 1) - 0.5) * 2;
            this.pointer.y = ((event.clientY - box.top) / Math.max(box.height, 1) - 0.5) * 2;
            this.rotationTarget.y = clamp(this.pointer.x * 0.45, -0.55, 0.55);
            this.rotationTarget.x = clamp(-0.12 - this.pointer.y * 0.24, -0.46, 0.18);
        }

        resize() {
            if (!this.renderer || !this.camera || !this.sceneEl) return;
            const rect = this.sceneEl.getBoundingClientRect();
            const width = Math.max(rect.width, 320);
            const height = Math.max(rect.height, 220);
            this.renderer.setSize(width, height, false);
            this.camera.aspect = width / height;
            this.camera.updateProjectionMatrix();
            this.applyLayout();
        }

        update(model) {
            this.model = normalizedModel(model);
            this.renderSide();
            if (!window.THREE || !this.renderer) {
                this.renderFallback();
                return;
            }
            this.rebuildScene();
        }

        renderSide() {
            if (!this.sideEl) return;
            const model = this.model;
            const metrics = model.metrics.length ? model.metrics : [
                { label: '节点', value: model.nodes.length },
                { label: '连接', value: model.links.length },
            ];
            const legendItems = model.legend.length
                ? model.legend
                : Array.from(new Set(model.nodes.map(node => node.type))).slice(0, 6).map(type => ({
                    label: TYPE_LABELS[type] || type,
                    color: COLORS[type] || COLORS.stage,
                }));
            this.sideEl.innerHTML = `
                <div>
                    <p class="three-workspace-kicker">${truncate(this.options.kicker || 'Workspace Map', 24)}</p>
                    <h3 class="three-workspace-title">${escapeHtml(truncate(model.title || this.options.title || '工作流图谱', 40))}</h3>
                    <p class="three-workspace-subtitle">${escapeHtml(truncate(model.subtitle || this.options.subtitle || '', 120))}</p>
                    <div class="three-workspace-metrics">
                        ${metrics.slice(0, 4).map(metric => `
                            <div class="three-workspace-metric">
                                <span>${escapeHtml(metric.label || '-')}</span>
                                <strong>${escapeHtml(String(metric.value ?? '-'))}</strong>
                            </div>
                        `).join('')}
                    </div>
                </div>
                <div class="three-workspace-legend">
                    ${legendItems.slice(0, 8).map(item => `
                        <span><i style="--tw-node-color:${escapeHtml(item.color || COLORS.stage)}"></i>${escapeHtml(item.label || '-')}</span>
                    `).join('')}
                </div>
            `;
        }

        rebuildScene() {
            const THREE = window.THREE;
            this.clearGroup();
            const nodes = this.model.nodes;
            const rect = this.sceneEl.getBoundingClientRect();
            const positions = computeLayout(nodes, Math.max(rect.width, 320), Math.max(rect.height, 220), this.model.layout);
            this.positions = positions;

            nodes.forEach(node => {
                const color = new THREE.Color(nodeColor(node));
                const radius = clamp(8 + Number(node.value || 0) * 1.3, 9, 22);
                const geometry = node.type === 'stage'
                    ? new THREE.BoxGeometry(radius * 1.65, radius * 1.65, radius * 1.65)
                    : new THREE.IcosahedronGeometry(radius, 2);
                const material = new THREE.MeshStandardMaterial({
                    color,
                    roughness: 0.48,
                    metalness: 0.12,
                    emissive: color,
                    emissiveIntensity: node.status === 'success' ? 0.18 : 0.08,
                });
                const mesh = new THREE.Mesh(geometry, material);
                const pos = positions.get(node.id) || { x: 0, y: 0, z: 0 };
                mesh.position.set(pos.x, pos.y, pos.z);
                mesh.userData.node = node;
                this.group.add(mesh);
                this.nodeMeshes.set(node.id, mesh);
                this.createLabel(node);
            });

            this.rebuildLines();
        }

        clearGroup() {
            if (!this.group) return;
            this.nodeMeshes.clear();
            while (this.group.children.length) {
                const child = this.group.children.pop();
                child.traverse?.(obj => {
                    obj.geometry?.dispose?.();
                    obj.material?.dispose?.();
                });
            }
            this.lineGroup = new window.THREE.Group();
            this.group.add(this.lineGroup);
            if (this.labelLayer) this.labelLayer.innerHTML = '';
            this.labels.clear();
        }

        createLabel(node) {
            if (!this.labelLayer || node.showLabel === false) return;
            const label = document.createElement('div');
            label.className = 'three-workspace-label';
            label.dataset.type = node.type || '';
            label.dataset.tone = ['idle', 'stage'].includes(node.status || node.type) ? 'muted' : '';
            const labelMax = Number(node.labelMax || (node.primary ? 34 : 24));
            label.textContent = truncate(node.label || node.id, Number.isFinite(labelMax) ? labelMax : 24);
            this.labelLayer.appendChild(label);
            this.labels.set(node.id, label);
        }

        rebuildLines() {
            const THREE = window.THREE;
            this.model.links.forEach(link => {
                const source = this.positions.get(link.source);
                const target = this.positions.get(link.target);
                if (!source || !target) return;
                const geometry = new THREE.BufferGeometry().setFromPoints([
                    new THREE.Vector3(source.x, source.y, source.z),
                    new THREE.Vector3(target.x, target.y, target.z),
                ]);
                const material = new THREE.LineBasicMaterial({
                    color: link.color || '#94a3b8',
                    transparent: true,
                    opacity: link.opacity ?? 0.42,
                });
                this.lineGroup.add(new THREE.Line(geometry, material));
            });
        }

        applyLayout() {
            if (!this.model?.nodes?.length || !this.nodeMeshes.size) return;
            const rect = this.sceneEl.getBoundingClientRect();
            this.positions = computeLayout(this.model.nodes, Math.max(rect.width, 320), Math.max(rect.height, 220), this.model.layout);
            this.nodeMeshes.forEach((mesh, id) => {
                const pos = this.positions.get(id);
                if (pos) mesh.position.set(pos.x, pos.y, pos.z);
            });
            if (this.lineGroup) {
                while (this.lineGroup.children.length) {
                    const child = this.lineGroup.children.pop();
                    child.geometry?.dispose?.();
                    child.material?.dispose?.();
                }
                this.rebuildLines();
            }
        }

        animate() {
            this.animationId = window.requestAnimationFrame(() => this.animate());
            if (!this.renderer || !this.scene || !this.camera || !this.group) return;
            const t = performance.now() * 0.001;
            this.rotation.x += (this.rotationTarget.x - this.rotation.x) * 0.05;
            this.rotation.y += (this.rotationTarget.y + Math.sin(t * 0.24) * 0.08 - this.rotation.y) * 0.05;
            this.group.rotation.x = this.rotation.x;
            this.group.rotation.y = this.rotation.y;
            this.nodeMeshes.forEach((mesh, id) => {
                const node = mesh.userData.node || {};
                const pulse = node.status === 'running' || node.status === 'success' ? 1.8 : 0.8;
                mesh.rotation.x += 0.004 + pulse * 0.0008;
                mesh.rotation.y += 0.006 + pulse * 0.0008;
                mesh.position.z += Math.sin(t * 1.4 + String(id).length) * 0.025;
            });
            this.renderer.render(this.scene, this.camera);
            this.updateLabels();
        }

        updateLabels() {
            if (!this.labelLayer || !this.camera || !this.group) return;
            const THREE = window.THREE;
            const rect = this.sceneEl.getBoundingClientRect();
            const vector = new THREE.Vector3();
            this.nodeMeshes.forEach((mesh, id) => {
                const label = this.labels.get(id);
                if (!label) return;
                vector.copy(mesh.position);
                this.group.localToWorld(vector);
                vector.project(this.camera);
                const x = (vector.x * 0.5 + 0.5) * rect.width;
                const y = (-vector.y * 0.5 + 0.5) * rect.height;
                const visible = vector.z < 1 && x >= -80 && x <= rect.width + 80 && y >= -40 && y <= rect.height + 40;
                label.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px) translate(-50%, -50%)`;
                label.style.opacity = visible ? '1' : '0';
            });
        }

        renderFallback() {
            if (!this.sceneEl) return;
            const rows = [];
            const nodes = this.model.nodes.slice(0, 18);
            for (let i = 0; i < nodes.length; i += 6) rows.push(nodes.slice(i, i + 6));
            this.sceneEl.innerHTML = `
                <div class="three-workspace-fallback">
                    ${rows.map(row => `
                        <div class="three-workspace-fallback-row">
                            ${row.map(node => `
                                <span class="three-workspace-fallback-node" style="border-color:${escapeHtml(nodeColor(node))}">
                                    ${escapeHtml(truncate(node.label || node.id, 22))}
                                </span>
                            `).join('')}
                        </div>
                    `).join('')}
                </div>
            `;
            this.renderSide();
        }

        destroy() {
            if (this.animationId) window.cancelAnimationFrame(this.animationId);
            this.clearGroup();
            this.renderer?.dispose?.();
        }
    }

    function escapeHtml(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    window.ShioaThreeGraph = {
        mount(root, options) {
            return new WorkspaceGraph(root, options);
        },
        colors: COLORS,
    };
})();
