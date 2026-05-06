const API_BASE = window.location.origin;
        const WS_BASE = (window.location.protocol === 'https:' ? 'wss' : 'ws') + '://' + window.location.hostname + ':32996';
        
        let refreshInterval;
        let allDevices = [];
        let selectedDeviceIp = null;
        let currentSession = null;
        let currentMenu = 'interfaces';
        let deviceNameWs;
        let advancedModeEnabled = false;
        
        function startAutoRefresh() {
            refreshInterval = setInterval(fetchDevices, 5000);
        }
        
        function stopAutoRefresh() {
            if (refreshInterval) {
                clearInterval(refreshInterval);
            }
        }
        
        async function fetchDevices() {
            try {
                const response = await fetch(`${API_BASE}/api/devices`);
                const devices = await response.json();
                allDevices = devices;
                displayDevices(devices);
            } catch (error) {
                console.error('Error fetching devices:', error);
            }
        }
        
        function displayDevices(devices) {
            const deviceList = document.getElementById('device-list');
            const deviceCount = document.getElementById('device-count');

            deviceCount.textContent = `${devices.length} 台设备`;

            if (devices.length === 0) {
                deviceList.innerHTML = `
                    <div class="empty-state">
                        <svg viewBox="0 0 24 24" fill="currentColor">
                            <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 17.93c-3.95-.49-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39z"/>
                        </svg>
                        <p>暂未发现设备</p>
                        <small>请确保设备已开启网络发现功能</small>
                    </div>
                `;
                return;
            }

            const sortedDevices = sortDevices(devices);

            let tableHtml = `
                <table class="device-table">
                    <thead>
                        <tr>
                            <th data-sort="status">状态</th>
                            <th data-sort="identity" class="sortable">
                                设备名称
                                <span class="sort-indicator">${getSortIndicator('identity')}</span>
                            </th>
                            <th data-sort="ip" class="sortable">
                                IP地址
                                <span class="sort-indicator">${getSortIndicator('ip')}</span>
                            </th>
                            <th data-sort="mac" class="sortable">
                                MAC地址
                                <span class="sort-indicator">${getSortIndicator('mac')}</span>
                            </th>
                            <th data-sort="version" class="sortable">
                                版本
                                <span class="sort-indicator">${getSortIndicator('version')}</span>
                            </th>
                            <th data-sort="interface" class="sortable">
                                发现接口
                                <span class="sort-indicator">${getSortIndicator('interface')}</span>
                            </th>
                            <th data-sort="uptime" class="sortable">
                                运行时间
                                <span class="sort-indicator">${getSortIndicator('uptime')}</span>
                            </th>
                        </tr>
                    </thead>
                    <tbody>
            `;

            sortedDevices.forEach(function(device) {
                const identity = device['Identity'] || device.identity || 'Unknown';
                const ip = device['IPv4-Address'] || device.ipv4_address || device.ip || '未配置IP地址';
                const mac = device['MAC-Address'] || device.mac_address || 'N/A';
                let version = device['Version'] || device.version || 'N/A';
                const uptime = device['Uptime'] || device.uptime || 'N/A';
                const discoveredInterface = device['Interface name'] || device.interface_name || '--';
                const isSelected = selectedDeviceIp === ip;
                
                if (version !== 'N/A') {
                    version = version.replace(/\s+\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}$/, '');
                }

                tableHtml += `
                    <tr onclick="selectDevice('${ip}', '${identity}', '${mac}')" ondblclick="doubleClickDevice('${ip}', '${identity}', '${mac}')" oncontextmenu="showDeviceContextMenu(event, '${ip}', '${identity}', '${mac}')" class="${isSelected ? 'selected' : ''}">
                        <td><span class="device-status"></span></td>
                        <td class="device-name">${identity}</td>
                        <td class="device-ip">${ip}</td>
                        <td class="device-mac">${mac}</td>
                        <td>${version}</td>
                        <td>${discoveredInterface}</td>
                        <td>${uptime}</td>
                    </tr>
                `;
            });

            tableHtml += '</tbody></table>';
            deviceList.innerHTML = tableHtml;

            document.querySelectorAll('.device-table th.sortable').forEach(th => {
                th.addEventListener('click', function() {
                    const sortKey = this.getAttribute('data-sort');
                    handleSort(sortKey);
                });
            });
        }

        function getSortIndicator(key) {
            if (sortConfig.key !== key) {
                return '';
            }
            return sortConfig.direction === 'asc' ? '<small class="sort-hint">升序</small>' : '<small class="sort-hint">降序</small>';
        }

        function handleSort(key) {
            if (sortConfig.key === key) {
                sortConfig.direction = sortConfig.direction === 'asc' ? 'desc' : 'asc';
            } else {
                sortConfig.key = key;
                sortConfig.direction = 'asc';
            }
            if (currentSearchKeyword) {
                const keyword = currentSearchKeyword.toLowerCase();
                const filtered = allDevices.filter(function(device) {
                    const identity = (device['Identity'] || device.identity || '').toLowerCase();
                    const ip = (device['IPv4-Address'] || device.ipv4_address || device.ip || '').toLowerCase();
                    const mac = (device['MAC-Address'] || device.mac_address || '').toLowerCase();
                    return identity.includes(keyword) || ip.includes(keyword) || mac.includes(keyword);
                });
                displayDevices(filtered);
            } else {
                displayDevices(allDevices);
            }
        }

        function naturalSort(a, b) {
            const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });
            return collator.compare(a, b);
        }

        function sortDevices(devices) {
            if (!sortConfig.key) return devices;

            return [...devices].sort((a, b) => {
                let valA, valB;

                switch (sortConfig.key) {
                    case 'identity':
                        valA = (a['Identity'] || a.identity || '').toString();
                        valB = (b['Identity'] || b.identity || '').toString();
                        break;
                    case 'ip':
                        valA = (a['IPv4-Address'] || a.ipv4_address || a.ip || '').toString();
                        valB = (b['IPv4-Address'] || b.ipv4_address || b.ip || '').toString();
                        break;
                    case 'mac':
                        valA = (a['MAC-Address'] || a.mac_address || '').toString();
                        valB = (b['MAC-Address'] || b.mac_address || '').toString();
                        break;
                    case 'version':
                        valA = (a['Version'] || a.version || '').toString();
                        valB = (b['Version'] || b.version || '').toString();
                        break;
                    case 'interface':
                        valA = (a['Interface name'] || a.interface_name || '').toString();
                        valB = (b['Interface name'] || b.interface_name || '').toString();
                        break;
                    case 'uptime':
                        valA = (a['Uptime'] || a.uptime || '').toString();
                        valB = (b['Uptime'] || b.uptime || '').toString();
                        break;
                    default:
                        return 0;
                }

                const result = naturalSort(valA, valB);
                return sortConfig.direction === 'asc' ? result : -result;
            });
        }

        let selectedDeviceMac = null;
        let currentSearchKeyword = '';
        let sortConfig = { key: null, direction: 'asc' };

        function selectDevice(ip, identity, mac) {
            selectedDeviceIp = ip;
            selectedDeviceMac = mac;
            document.getElementById('device-ip').value = ip;
            if (identity !== 'Unknown') {
                document.getElementById('username').value = 'admin';
            }
            if (currentSearchKeyword) {
                const keyword = currentSearchKeyword.toLowerCase();
                const filtered = allDevices.filter(function(device) {
                    const devIdentity = (device['Identity'] || device.identity || '').toLowerCase();
                    const devIp = (device['IPv4-Address'] || device.ipv4_address || device.ip || '').toLowerCase();
                    const devMac = (device['MAC-Address'] || device.mac_address || '').toLowerCase();
                    return devIdentity.includes(keyword) || devIp.includes(keyword) || devMac.includes(keyword);
                });
                displayDevices(filtered);
            } else {
                displayDevices(allDevices);
            }
        }

        function doubleClickDevice(ip, identity, mac) {
            selectDevice(ip, identity, mac);
            document.getElementById('login-btn').click();
        }

        let contextMenuDevice = null;

        function showDeviceContextMenu(e, ip, identity, mac) {
            e.preventDefault();
            selectDevice(ip, identity, mac);
            
            if (!advancedModeEnabled || !e.ctrlKey) {
                return;
            }
            
            contextMenuDevice = { ip, identity, mac };
            
            let contextMenu = document.getElementById('device-context-menu');
            if (!contextMenu) {
                contextMenu = document.createElement('div');
                contextMenu.id = 'device-context-menu';
                contextMenu.style.cssText = 'position: fixed; background: #fff; border: 1px solid #ddd; border-radius: 4px; box-shadow: 0 2px 10px rgba(0,0,0,0.15); z-index: 10000; min-width: 120px;';
                document.body.appendChild(contextMenu);
            }
            
            contextMenu.innerHTML = `
                <div id="slsc-menu-item" style="padding: 8px 16px; cursor: pointer; font-size: 14px;" onmouseover="this.style.background='#f5f5f5'" onmouseout="this.style.background='transparent'">
                    英文工具
                </div>
            `;
            
            document.getElementById('slsc-menu-item').onclick = function() {
                openSLSCtools(mac);
            };
            
            contextMenu.style.left = e.clientX + 'px';
            contextMenu.style.top = e.clientY + 'px';
            contextMenu.style.display = 'block';
        }

        document.addEventListener('click', function(e) {
            const contextMenu = document.getElementById('device-context-menu');
            if (contextMenu && !contextMenu.contains(e.target)) {
                contextMenu.style.display = 'none';
            }
        });

        async function openSLSCtools(mac) {
            const contextMenu = document.getElementById('device-context-menu');
            if (contextMenu) {
                contextMenu.style.display = 'none';
            }
            
            console.log('调用 openSLSCtools, mac:', mac);
            
            try {
                const response = await fetch(`${API_BASE}/api/slsc-tools`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ mac: mac })
                });
                const result = await response.json();
                console.log('响应:', result);
                if (result.status !== 'success') {
                    alert('启动失败: ' + result.message);
                }
            } catch (error) {
                console.error('启动错误:', error);
                alert('启动失败');
            }
        }

        document.getElementById('device-search').addEventListener('input', function() {
            currentSearchKeyword = this.value;
            const keyword = this.value.toLowerCase();
            const filtered = allDevices.filter(function(device) {
                const identity = (device['Identity'] || device.identity || '').toLowerCase();
                const ip = (device['IPv4-Address'] || device.ipv4_address || device.ip || '').toLowerCase();
                const mac = (device['MAC-Address'] || device.mac_address || '').toLowerCase();
                return identity.includes(keyword) || ip.includes(keyword) || mac.includes(keyword);
            });
            displayDevices(filtered);
        });

        function togglePasswordVisibility() {
            const passwordInput = document.getElementById('password');
            const showPasswordCheckbox = document.getElementById('show-password');
            passwordInput.type = showPasswordCheckbox.checked ? 'text' : 'password';
        }

        document.getElementById('password').addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                document.getElementById('login-btn').click();
            }
        });

        // 组合键监听：Ctrl+Shift+S
        document.addEventListener('keydown', function(e) {
            if (e.ctrlKey && e.shiftKey && e.key.toLowerCase() === 's') {
                e.preventDefault();
                showSecretModal();
            }
        });

        // 显示高级模式弹窗
        function showSecretModal() {
            // 检查是否已存在弹窗
            let existingModal = document.getElementById('secret-modal');
            if (existingModal) {
                existingModal.classList.add('active');
                return;
            }

            // 创建弹窗
            const modal = document.createElement('div');
            modal.id = 'secret-modal';
            modal.className = 'modal active';
            modal.innerHTML = `
                <div class="modal-content" style="max-width: 400px;">
                    <div class="modal-header">
                        <h3>高级模式</h3>
                        <button class="modal-close" onclick="closeSecretModal()">&times;</button>
                    </div>
                    <div class="modal-body" style="padding: 20px;">
                        <div style="display: flex; flex-direction: column; gap: 15px;">
                            <button class="btn btn-login" style="width: 100%; padding: 15px; font-size: 16px;" onclick="unlockEnglishTool()">
                                🔓 解锁英文工具
                            </button>
                            <button class="btn btn-refresh" style="width: 100%; padding: 15px; font-size: 16px;" onclick="enableCompatibilityMode()">
                                🔧 兼容模式
                            </button>
                        </div>
                    </div>
                </div>
            `;
            document.body.appendChild(modal);

            // 点击背景关闭
            modal.addEventListener('click', function(e) {
                if (e.target === modal) {
                    closeSecretModal();
                }
            });
        }

        function closeSecretModal() {
            const modal = document.getElementById('secret-modal');
            if (modal) {
                modal.classList.remove('active');
            }
        }

        function unlockEnglishTool() {
            closeSecretModal();
            advancedModeEnabled = true;
        }

        function enableCompatibilityMode() {
            closeSecretModal();
        }

        window.addEventListener('beforeunload', function() {
            if (advancedModeEnabled) {
                navigator.sendBeacon(`${API_BASE}/api/slsc-tools/close`);
            }
        });

        document.getElementById('refresh-btn').addEventListener('click', async function() {
            const deviceList = document.getElementById('device-list');

            deviceList.innerHTML = `
                <div class="empty-state">
                    <svg viewBox="0 0 24 24" fill="currentColor" style="animation: spin 1s linear infinite;">
                        <path d="M12 4V1L8 5l4 4V6c3.31 0 6 2.69 6 6 0 1.01-.25 1.97-.7 2.8l1.46 1.46C19.54 15.03 20 13.57 20 12c0-4.42-3.58-8-8-8zm0 14c-3.31 0-6-2.69-6-6 0-1.01.25-1.97.7-2.8L5.24 7.74C4.46 8.97 4 10.43 4 12c0 4.42 3.58 8 8 8v3l4-4-4-4v3z"/>
                    </svg>
                    <p>正在搜索设备...</p>
                    <small id="search-status">正在发送网络发现请求...</small>
                </div>
            `;

            try {
                // 调用刷新接口：后端清空列表 → 发发现包 → 等3秒 → 返回在线设备
                const refreshResponse = await fetch(`${API_BASE}/api/refresh`, { method: 'POST' });
                if (!refreshResponse.ok) {
                    throw new Error(`HTTP error: ${refreshResponse.status}`);
                }
                const refreshDevices = await refreshResponse.json();
                allDevices = refreshDevices;
                displayDevices(refreshDevices);

                const searchStatus = document.getElementById('search-status');
                if (searchStatus) {
                    searchStatus.textContent = '等待设备响应...';
                }

                // 继续轮询2秒，看有没有更多设备出现
                let waitCount = 0;
                const maxWaitTime = 2000;
                const intervalTime = 500;

                const pollDevices = async () => {
                    await fetchDevices();
                    waitCount += intervalTime;
                    if (waitCount < maxWaitTime) {
                        setTimeout(pollDevices, intervalTime);
                    } else {
                        const devices = document.querySelectorAll('.device-table tbody tr');
                        if (devices.length === 0) {
                            deviceList.innerHTML = `
                                <div class="empty-state">
                                    <svg viewBox="0 0 24 24" fill="currentColor">
                                        <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/>
                                    </svg>
                                    <p>未发现设备</p>
                                    <small>请确保设备已开启网络发现功能</small>
                                </div>
                            `;
                        }
                    }
                };

                setTimeout(pollDevices, intervalTime);
            } catch (error) {
                deviceList.innerHTML = `
                    <div class="empty-state">
                        <svg viewBox="0 0 24 24" fill="currentColor" style="color: #f44336;">
                            <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/>
                        </svg>
                        <p>连接服务器失败</p>
                    </div>
                `;
            }
        });
        
        document.getElementById('login-btn').addEventListener('click', async function() {
            const ip = document.getElementById('device-ip').value.trim();
            const username = document.getElementById('username').value.trim();
            const password = document.getElementById('password').value;
            
            if (!ip) {
                showLoginErrorModal('请输入设备IP地址');
                return;
            }
            
            if (!username) {
                showLoginErrorModal('请输入账号');
                return;
            }
            
            const btn = this;
            btn.disabled = true;
            btn.textContent = '登录中...';
            
            try {
                const response = await fetch(`${API_BASE}/api/connect`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ ip, username, password })
                });
                
                const result = await response.json();
                
                if (result.status === 'success') {
                    currentSession = {
                        ip: result.ip,
                        mac: selectedDeviceMac || '',
                        username: result.username,
                        password: password,
                        api_version: result.api_version,
                        routeros_version: result.routeros_version,
                        board_name: result.board_name,
                        identity: result.identity || result.ip
                    };
                    
                    showConfigInterface();
                } else {
                    let errorMsg = result.message || '登录失败';
                    const msg = (result.message || '').toLowerCase();
                    
                    if (msg.includes('timeout') || msg.includes('timed out') || msg.includes('超时')) {
                        errorMsg = '连接超时，请检查设备IP地址是否正确';
                    } else if (msg.includes('refused') || msg.includes('connection refused') || msg.includes('连接被拒绝')) {
                        errorMsg = '连接被拒绝，请检查设备服务是否开启';
                    } else if (msg.includes('no route') || msg.includes('unreachable') || msg.includes('不可达')) {
                        errorMsg = '设备不可达，请检查网络连接';
                    } else if (msg.includes('用户名或密码错误') || msg.includes('password') || msg.includes('auth') || msg.includes('credential')) {
                        errorMsg = '用户名或密码错误';
                    } else if (msg.includes('请输入')) {
                        errorMsg = result.message;
                    } else if (msg.includes('legacy api:') || msg.includes('legacy ssl api:')) {
                        errorMsg = '无法连接设备，请检查IP地址和端口是否开放';
                    }
                    
                    showLoginErrorModal(errorMsg);
                }
            } catch (error) {
                showLoginErrorModal('连接服务器失败，请确保后端服务已启动');
            } finally {
                btn.disabled = false;
                btn.textContent = '登录';
            }
        });
        


        function updateDeviceStatus(status) {
            const statusEl = document.getElementById('sidebar-device-status');
            const statusText = document.getElementById('status-text');
            if (status === 'online') {
                statusEl.classList.remove('offline');
                statusText.textContent = '设备在线';
            } else {
                statusEl.classList.add('offline');
                statusText.textContent = '设备离线';
            }
        }

        function openDeviceNameModal() {
            const modal = document.getElementById('device-name-modal');
            const input = document.getElementById('device-name-input');
            if (!modal || !input) return;
            
            const currentName = document.getElementById('sidebar-device-name').textContent.replace('设备名称：', '');
            input.value = currentName === '网络设备' ? '' : currentName;
            modal.classList.add('active');
            
            setTimeout(function() {
                input.focus();
                input.select();
            }, 100);
        }

        function closeDeviceNameModal() {
            const modal = document.getElementById('device-name-modal');
            if (modal) {
                modal.classList.remove('active');
            }
        }



        function saveDeviceName() {
            const input = document.getElementById('device-name-input');
            if (!input) return;
            
            const newName = input.value.trim();
            if (!newName) {
                showNetworkAlert('请输入设备名称', 'error');
                return;
            }

            if (deviceNameWs && deviceNameWs.readyState === WebSocket.OPEN) {
                deviceNameWs.close();
            }

            const wsUrl = `${WS_BASE}`;
            deviceNameWs = new WebSocket(wsUrl);

            deviceNameWs.onopen = function() {
                console.log('[设备名称] WebSocket已连接');
                deviceNameWs.send(JSON.stringify({
                    ip: currentSession.ip,
                    mac: currentSession.mac || '',
                    username: currentSession.username,
                    password: currentSession.password || '',
                    action: 'set_device_name',
                    name: newName
                }));
            };

            deviceNameWs.onmessage = function(event) {
                try {
                    const data = JSON.parse(event.data);
                    console.log('[设备名称] 收到消息:', data);
                    
                    if (data.status === 'success') {
                        document.getElementById('sidebar-device-name').textContent = '设备名称：' + newName;
                        showNetworkAlert('设备名称修改成功', 'success');
                        closeDeviceNameModal();
                        
                        if (currentSession) {
                            currentSession.identity = newName;
                        }
                    } else {
                        showNetworkAlert(data.message || '修改失败', 'error');
                    }
                } catch (e) {
                    console.error('[设备名称] 解析消息失败:', e);
                } finally {
                    if (deviceNameWs) {
                        deviceNameWs.close();
                        deviceNameWs = null;
                    }
                }
            };

            deviceNameWs.onerror = function(error) {
                console.error('[设备名称] WebSocket错误:', error);
                showNetworkAlert('连接失败', 'error');
            };

            deviceNameWs.onclose = function() {
                console.log('[设备名称] WebSocket已关闭');
                deviceNameWs = null;
            };
        }

        function showConfigInterface() {
            isLoggingOut = false;

            document.getElementById('login-container').classList.add('hidden');
            document.getElementById('config-container').classList.add('active');

            const sidebar = document.getElementById('sidebar');
            const logoutBtn = document.getElementById('logout-btn');
            if (sidebar) sidebar.style.display = 'flex';
            if (logoutBtn) logoutBtn.style.display = 'block';

            console.log('currentSession:', currentSession);
            console.log('identity:', currentSession.identity);
            console.log('board_name:', currentSession.board_name);

            const deviceName = currentSession.identity || currentSession.board_name || '网络设备';
            document.getElementById('sidebar-device-name').textContent = '设备名称：' + deviceName;
            document.getElementById('sidebar-device-ip').textContent = '当前设备 IP 地址：' + currentSession.ip;

            document.querySelectorAll('.menu-item').forEach(i => i.classList.remove('active'));
            document.querySelectorAll('.submenu-item').forEach(i => i.classList.remove('active'));
            const deviceInfoMenu = document.querySelector('.menu-item[data-menu="device-info"]');
            if (deviceInfoMenu) deviceInfoMenu.classList.add('active');

            connectWebSocket();

            loadMenuContent('device-info');
        }

        function hideConfigInterface() {
            stopArpPolling();
            stopLogPolling();
            clearReconnectTimer();
            stopWsHeartbeatCheck();
            disconnectInterfaceWebSocket();

            clearDeviceTimeCache();
            lastTrafficData = {};
            interfaceCache = {};
            interfaceMissCount = {};

            isLoggingOut = true;

            if (ws) {
                ws.close();
                ws = null;
            }

            stopWsHeartbeatCheck();

            if (currentSession && currentSession.ip) {
                fetch(`${API_BASE}/api/logout`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ ip: currentSession.ip, mac: currentSession.mac || '' })
                })
                    .then(response => response.json())
                    .then(data => console.log('断开连接结果:', data))
                    .catch(err => console.error('断开连接失败:', err));
            }

            currentSession = null;

            const loginContainer = document.getElementById('login-container');
            const configContainer = document.getElementById('config-container');
            const logoutBtn = document.getElementById('logout-btn');
            const sidebar = document.getElementById('sidebar');
            const reconnectModal = document.getElementById('reconnect-modal');

            if (loginContainer) loginContainer.classList.remove('hidden');
            if (configContainer) configContainer.classList.remove('active');
            if (logoutBtn) logoutBtn.style.display = 'none';
            if (sidebar) sidebar.style.display = 'none';
            if (reconnectModal) reconnectModal.classList.remove('active');

            if (typeof stopAllPolling === 'function') {
                stopAllPolling();
            }
        }

        document.querySelectorAll('.menu-item').forEach(function(item) {
            item.addEventListener('click', function() {
                const menu = this.dataset.menu;
                
                if (menu === 'wireless' || menu === 'system') {
                    this.classList.toggle('expanded');
                    const submenu = this.nextElementSibling;
                    if (submenu && submenu.classList.contains('submenu')) {
                        submenu.classList.toggle('show');
                    }
                    return;
                }
                
                document.querySelectorAll('.menu-item').forEach(i => i.classList.remove('active'));
                document.querySelectorAll('.submenu-item').forEach(i => i.classList.remove('active'));
                this.classList.add('active');
                loadMenuContent(menu);
            });
        });
        
        document.querySelectorAll('.submenu-item').forEach(function(item) {
            item.addEventListener('click', function(e) {
                e.stopPropagation();
                document.querySelectorAll('.menu-item').forEach(i => i.classList.remove('active'));
                document.querySelectorAll('.submenu-item').forEach(i => i.classList.remove('active'));
                this.classList.add('active');
                loadMenuContent(this.dataset.menu);
            });
        });

        async function handleLogout() {
            if (currentSession) {
                try {
                    const response = await fetch(`${API_BASE}/api/logout`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ ip: currentSession.ip, mac: currentSession.mac || '' })
                    });
                    const result = await response.json();
                    console.log('登出结果:', result);
                } catch (error) {
                    console.error('登出失败:', error);
                }
            }
            hideConfigInterface();
        }
        
        function loadMenuContent(menu) {
            if (currentMenu !== menu) {
                if (currentMenu === 'interfaces') {
                    disconnectInterfaceWebSocket();
                    hideInterfaceDetail();
                    selectedInterface = null;
                }
                if (currentMenu === 'wireless-interfaces') {
                    disconnectWirelessInterfacesWebSocket();
                }
                if (currentMenu === 'wireless-clients') {
                    disconnectWirelessClientsWebSocket();
                }
                if (currentMenu === 'wireless-security') {
                    disconnectSecurityProfilesWebSocket();
                }
                if (currentMenu === 'network') {
                    disconnectNetworkWebSocket();
                }
                if (currentMenu === 'logs') {
                    stopLogPolling();
                }
            }
            
            currentMenu = menu;
            const titles = {
                'device-info': '设备信息',
                interfaces: '接口列表',
                bridge: '桥',
                wireless: '无线',
                'wireless-interfaces': '无线接口',
                'wireless-clients': '终端列表',
                'wireless-security': '加密配置',
                network: '网络',
                firewall: '防火墙',
                routing: '路由',
                system: '系统',
                logs: '日志',
                'device-name': '设备名称'
            };

            document.getElementById('content-title').textContent = titles[menu] || menu;

            if (menu === 'device-info') {
                loadDeviceInfoContent();
            } else if (menu === 'interfaces') {
                loadInterfacesContent();
            } else if (menu === 'wireless-interfaces') {
                loadWirelessInterfacesContent();
            } else if (menu === 'wireless-clients') {
                loadWirelessClientsContent();
            } else if (menu === 'wireless-security') {
                loadSecurityProfilesContent();
            } else if (menu === 'network') {
                loadNetworkContent();
            } else if (menu === 'logs') {
                loadLogsContent();
            } else if (menu === 'device-name') {
                loadDeviceNameContent();
            } else {
                document.getElementById('content-body').innerHTML = `
                    <div class="config-card">
                        <div class="config-card-header">
                            <h3>${titles[menu]}</h3>
                        </div>
                        <div class="config-card-body">
                            <p style="color: #666; text-align: center; padding: 40px;">
                                ${titles[menu]}配置功能开发中...
                            </p>
                        </div>
                    </div>
                `;
            }
        }
        
        // ========== 日志功能 ==========
        let logWs = null;
        let logAutoScroll = true;
        let logSeqCounter = 0;

        function stopLogPolling() {
            if (logWs) {
                try {
                    logWs.close();
                } catch (e) {}
                logWs = null;
            }
        }

        function loadLogsContent() {
            logAutoScroll = true;
            logSeqCounter = 0;
            
            // 先关闭旧的 WebSocket 连接，避免旧连接继续推送日志
            stopLogPolling();
            
            // 等待一小段时间确保旧连接完全关闭
            setTimeout(() => {
                // 清空内容并创建新的 DOM
                const contentBody = document.getElementById('content-body');
                contentBody.innerHTML = `
                    <div class="config-card" style="height: calc(100vh - 160px); display: flex; flex-direction: column;">
                        <div class="config-card-header" style="display: flex; justify-content: space-between; align-items: center; flex-shrink: 0;">
                            <h3>设备日志</h3>
                            <div style="display: flex; gap: 8px; align-items: center;">
                                <label style="display: flex; align-items: center; gap: 4px; font-size: 12px; color: #aaa; cursor: pointer;">
                                    <input type="checkbox" id="log-auto-scroll" checked onchange="logAutoScroll = this.checked;">
                                    自动滚动
                                </label>
                                <span id="log-status" style="font-size: 11px; color: #888;"></span>
                                <button class="btn btn-refresh" onclick="clearLogs()" style="font-size: 12px; padding: 4px 10px;">清空</button>
                                <button class="btn btn-refresh" onclick="refreshLogs()" style="font-size: 12px; padding: 4px 10px;">刷新</button>
                            </div>
                        </div>
                        <div id="log-container" style="flex: 1; overflow-y: auto; background: #1e1e2e; border-radius: 6px; padding: 8px; font-family: 'Consolas', 'Monaco', monospace; font-size: 12px; line-height: 1.6;">
                            <div id="log-entries" style="white-space: pre-wrap; word-break: break-all;"></div>
                            <div id="log-loading" style="text-align: center; padding: 40px; color: #666;">
                                正在连接日志服务...
                            </div>
                        </div>
                    </div>
                `;

                const logContainer = document.getElementById('log-container');
                logContainer.addEventListener('scroll', function() {
                    const atBottom = logContainer.scrollHeight - logContainer.scrollTop - logContainer.clientHeight < 30;
                    logAutoScroll = atBottom;
                    const checkbox = document.getElementById('log-auto-scroll');
                    if (checkbox) checkbox.checked = logAutoScroll;
                });

                // 建立新的 WebSocket 连接
                connectLogWebSocket();
            }, 200);
        }

        function connectLogWebSocket() {
            if (!currentSession) return;
            stopLogPolling();

            const wsUrl = `${WS_BASE}`;
            logWs = new WebSocket(wsUrl);

            logWs.onopen = function() {
                console.log('[日志] WebSocket已连接');
                logWs.send(JSON.stringify({
                    ip: currentSession.ip,
                    mac: currentSession.mac,
                    username: currentSession.username,
                    password: currentSession.password,
                    is_logs: true
                }));
            };

            logWs.onmessage = function(event) {
                try {
                    const data = JSON.parse(event.data);
                    handleLogMessage(data);
                } catch (e) {
                    console.error('[日志] 解析消息失败:', e);
                }
            };

            logWs.onerror = function(error) {
                console.error('[日志] WebSocket错误:', error);
                const logLoading = document.getElementById('log-loading');
                if (logLoading) logLoading.textContent = '连接错误';
            };

            logWs.onclose = function() {
                console.log('[日志] WebSocket已关闭');
            };
        }

        function handleLogMessage(data) {
            const logLoading = document.getElementById('log-loading');
            const logStatus = document.getElementById('log-status');

            switch (data.status) {
                case 'connected':
                    if (logLoading) logLoading.textContent = '正在获取日志...';
                    break;

                case 'downloading':
                    if (logLoading) logLoading.textContent = data.message || '正在通过FTP下载日志...';
                    if (logStatus) logStatus.textContent = 'FTP下载中...';
                    break;

                case 'cache_info':
                    if (logLoading) logLoading.textContent = `加载缓存日志 (${data.total} 条)...`;
                    break;

                case 'batch':
                    appendLogEntries(data.logs);
                    if (logLoading) {
                        const progress = Math.round(((data.offset + data.logs.length) / data.total) * 100);
                        logLoading.textContent = `加载中... ${progress}% (${data.offset + data.logs.length}/${data.total})`;
                    }
                    break;

                case 'ftp_done':
                    if (logLoading) logLoading.style.display = 'none';
                    if (logStatus) logStatus.textContent = `${data.total} 条日志`;
                    break;

                case 'ftp_failed':
                    if (logLoading) logLoading.textContent = data.message || 'FTP下载失败，改用其他方式获取...';
                    break;

                case 'incremental':
                    appendLogEntries(data.logs);
                    if (logStatus) {
                        const entries = document.getElementById('log-entries');
                        const count = entries ? entries.children.length : 0;
                        logStatus.textContent = `${count} 条日志 (实时)`;
                    }
                    break;

                case 'error':
                    if (logLoading) logLoading.textContent = '错误: ' + (data.message || '未知错误');
                    if (logStatus) logStatus.textContent = '错误';
                    break;
            }
        }

        function getLogTopicClass(topics) {
            if (!topics) return 'log-topic-info';
            const t = topics.toLowerCase();
            if (t.includes('error') || t.includes('critical')) return 'log-topic-error';
            if (t.includes('warning') || t.includes('warn')) return 'log-topic-warning';
            if (t.includes('info')) return 'log-topic-info';
            if (t.includes('debug')) return 'log-topic-debug';
            return 'log-topic-info';
        }

        function appendLogEntries(logs) {
            const logEntries = document.getElementById('log-entries');
            const logContainer = document.getElementById('log-container');
            const logLoading = document.getElementById('log-loading');
            if (!logEntries) return;

            if (!logs || logs.length === 0) return;

            const fragment = document.createDocumentFragment();
            logs.forEach(log => {
                const seq = log.seq !== undefined ? log.seq : logSeqCounter++;
                if (log.seq === undefined) logSeqCounter = seq + 1;
                const time = log.time || '';
                const topics = log.topics || '';
                const message = log.message || '';
                const topicClass = getLogTopicClass(topics);

                const entry = document.createElement('div');
                entry.style.cssText = 'border-bottom: 1px solid #2a2a3e; padding: 2px 4px;';
                entry.innerHTML = `<span style="color: #555; min-width: 40px; display: inline-block;">${seq}</span> <span style="color: #6a9955;">${time}</span> <span class="${topicClass}" style="padding: 1px 4px; border-radius: 3px; font-size: 11px;">${topics}</span> <span style="color: #d4d4d4;">${escapeHtml(message)}</span>`;
                fragment.appendChild(entry);
            });
            logEntries.appendChild(fragment);

            if (logAutoScroll && logContainer) {
                logContainer.scrollTop = logContainer.scrollHeight;
            }
        }

        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        function refreshLogs() {
            const logEntries = document.getElementById('log-entries');
            const logLoading = document.getElementById('log-loading');
            if (logEntries) logEntries.innerHTML = '';
            logSeqCounter = 0;
            if (logLoading) {
                logLoading.style.display = 'block';
                logLoading.textContent = '正在重新加载日志...';
            }
            connectLogWebSocket();
        }

        function clearLogs() {
            const logEntries = document.getElementById('log-entries');
            const logLoading = document.getElementById('log-loading');
            if (logEntries) logEntries.innerHTML = '';
            logSeqCounter = 0;
            if (logLoading) {
                logLoading.style.display = 'block';
                logLoading.textContent = '日志已清空（新日志仍会继续推送）';
            }
        }
        // ========== 日志功能结束 ==========
        
        function loadDeviceNameContent() {
            const contentBody = document.getElementById('content-body');
            const currentName = currentSession.identity || currentSession.board_name || '网络设备';
            
            contentBody.innerHTML = `
                <div class="config-card">
                    <div class="config-card-header">
                        <h3>设备名称</h3>
                    </div>
                    <div class="config-card-body">
                        <div style="max-width: 500px; margin: 0 auto;">
                            <div class="form-group">
                                <label for="device-name-input" style="display: block; margin-bottom: 10px; font-weight: 600; color: #2c3e50;">
                                    当前设备名称
                                </label>
                                <input type="text" id="device-name-input" value="${currentName}" 
                                    style="width: 100%; padding: 12px 15px; border: 1px solid #ddd; border-radius: 6px; font-size: 14px; margin-bottom: 20px;">
                            </div>
                            <div style="display: flex; gap: 10px;">
                                <button id="save-device-name-btn" class="btn btn-login" onclick="saveDeviceNameFromMenu()"
                                    style="flex: 1;">保存</button>
                                <button id="cancel-device-name-btn" class="btn" onclick="loadMenuContent('system')"
                                    style="flex: 1; background: #6c757d; color: white;">取消</button>
                            </div>
                        </div>
                    </div>
                </div>
            `;
        }
        
        function saveDeviceNameFromMenu() {
            const input = document.getElementById('device-name-input');
            if (!input) return;
            
            const newName = input.value.trim();
            if (!newName) {
                showNetworkAlert('请输入设备名称', 'error');
                return;
            }

            if (deviceNameWs && deviceNameWs.readyState === WebSocket.OPEN) {
                deviceNameWs.close();
            }

            const wsUrl = `${WS_BASE}`;
            deviceNameWs = new WebSocket(wsUrl);

            deviceNameWs.onopen = function() {
                console.log('[设备名称] WebSocket已连接');
                deviceNameWs.send(JSON.stringify({
                    ip: currentSession.ip,
                    mac: currentSession.mac || '',
                    username: currentSession.username,
                    password: currentSession.password || '',
                    action: 'set_device_name',
                    name: newName
                }));
            };

            deviceNameWs.onmessage = function(event) {
                try {
                    const data = JSON.parse(event.data);
                    console.log('[设备名称] 收到消息:', data);
                    
                    if (data.status === 'success') {
                        document.getElementById('sidebar-device-name').textContent = '设备名称：' + newName;
                        showNetworkAlert('设备名称修改成功', 'success');
                        
                        // 更新当前会话中的设备名称
                        if (currentSession) {
                            currentSession.identity = newName;
                        }
                        
                        // 刷新设备名称显示
                        loadDeviceNameContent();
                    } else {
                        showNetworkAlert(data.message || '修改失败', 'error');
                    }
                } catch (e) {
                    console.error('[设备名称] 解析消息失败:', e);
                } finally {
                    if (deviceNameWs) {
                        deviceNameWs.close();
                        deviceNameWs = null;
                    }
                }
            };

            deviceNameWs.onerror = function(error) {
                console.error('[设备名称] WebSocket错误:', error);
                showNetworkAlert('连接失败', 'error');
            };

            deviceNameWs.onclose = function() {
                console.log('[设备名称] WebSocket已关闭');
                deviceNameWs = null;
            };
        }
        
        function loadWirelessInterfacesContent() {
            const contentBody = document.getElementById('content-body');
            contentBody.innerHTML = `
                <div class="config-card">
                    <div class="config-card-header" style="display: flex; justify-content: space-between; align-items: center;">
                        <h3>无线接口</h3>
                        <div class="interface-buttons">
                            <button class="btn btn-enable" id="btn-enable-wireless" onclick="handleEnableWirelessInterface()" disabled style="opacity: 0.5;">启用</button>
                            <button class="btn btn-disable" id="btn-disable-wireless" onclick="handleDisableWirelessInterface()" disabled style="opacity: 0.5;">禁用</button>
                            <button id="interference-scan-btn" class="btn btn-refresh" onclick="startInterferenceScan()">
                                干扰扫描
                            </button>
                        </div>
                    </div>
                    <div class="config-card-body">
                        <table class="data-table">
                            <thead>
                                <tr>
                                    <th>状态</th>
                                    <th>名称</th>
                                    <th>模式</th>
                                    <th>SSID</th>
                                    <th>频率</th>
                                    <th>频段</th>
                                    <th>频宽</th>
                                    <th>传输协议</th>
                                </tr>
                            </thead>
                            <tbody id="wireless-interfaces-tbody">
                                <tr>
                                    <td colspan="8" style="text-align: center; color: #666; padding: 20px;">
                                        加载中...
                                    </td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            `;
            connectWirelessInterfacesWebSocket();
        }
        
        let interferenceScanWs = null;
        let interferenceScanModal = null;
        
        function startInterferenceScan() {
            if (interferenceScanWs && interferenceScanWs.readyState === WebSocket.OPEN) {
                return;
            }
            
            const modal = document.createElement('div');
            modal.id = 'interference-scan-modal';
            modal.style.cssText = 'position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.5); display: flex; justify-content: center; align-items: center; z-index: 1000;';
            modal.innerHTML = `
                <div class="config-card" style="min-width: 800px; max-width: 1000px; margin: 0;">
                    <div class="config-card-header">
                        <h3>干扰扫描</h3>
                        <button onclick="closeInterferenceScanModal()" style="background: none; border: none; font-size: 20px; cursor: pointer; color: #666;">&times;</button>
                    </div>
                    <div class="config-card-body">
                        <div style="display: flex; gap: 15px; margin-bottom: 15px; align-items: center;">
                            <div style="flex: 1;">
                                <label style="display: block; margin-bottom: 6px; font-weight: 500; color: #2c3e50; font-size: 13px;">扫描接口</label>
                                <select id="scan-interface-select" style="width: 100%; padding: 8px 10px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px;">
                                    <option value="">加载中...</option>
                                </select>
                            </div>
                            <div style="flex: 1;">
                                <label style="display: block; margin-bottom: 6px; font-weight: 500; color: #2c3e50; font-size: 13px;">扫描模式</label>
                                <div style="display: flex; align-items: center; gap: 8px; padding: 8px 0;">
                                    <input type="checkbox" id="scan-background" style="width: 15px; height: 15px; cursor: pointer;">
                                    <label for="scan-background" style="font-size: 14px; color: #2c3e50; cursor: pointer;">后台扫描</label>
                                </div>
                            </div>
                            <div style="display: flex; gap: 8px; padding-top: 24px;">
                                <button id="scan-start-btn" class="btn btn-refresh" onclick="startInterferenceScanTask()">开始</button>
                                <button id="scan-stop-btn" class="btn btn-logout" onclick="stopInterferenceScanTask()" disabled style="opacity: 0.5;">停止</button>
                            </div>
                        </div>
                        <div id="scan-stats" style="display: none; padding: 8px 12px; background: #f5f7fa; border-radius: 4px; margin-bottom: 15px; font-size: 13px; color: #2c3e50;">
                            已扫描到 <span id="scan-count" style="font-weight: 600; color: #3498db;">0</span> 个基站
                        </div>
                        <div id="scan-result-container" style="max-height: 400px; overflow-y: auto; display: none; border-top: 1px solid #e0e0e0; padding-top: 15px;">
                            <table class="data-table" style="table-layout: fixed; width: 100%;">
                                <thead>
                                    <tr>
                                        <th class="sortable" style="width: 140px;" onclick="sortScanResults('address')">MAC地址</th>
                                        <th class="sortable" style="width: 150px;" onclick="sortScanResults('ssid')">SSID</th>
                                        <th class="sortable" style="width: 180px;" onclick="sortScanResults('channel')">信道</th>
                                        <th class="sortable" style="width: 100px;" onclick="sortScanResults('signal_strength')">信号强度</th>
                                        <th class="sortable" style="width: 100px;" onclick="sortScanResults('noise')">噪声</th>
                                        <th class="sortable" style="width: 100px;" onclick="sortScanResults('snr')">信噪比</th>
                                    </tr>
                                </thead>
                                <tbody id="scan-result-tbody">
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>
            `;
            document.body.appendChild(modal);
            interferenceScanModal = modal;
            
            modal.onclick = function(e) {
                if (e.target === modal) {
                    closeInterferenceScanModal();
                }
            };
            
            loadWirelessInterfacesForScan();
        }
        
        function loadWirelessInterfacesForScan() {
            const wsUrl = `${WS_BASE}`;
            const ws = new WebSocket(wsUrl);
            
            ws.onopen = function() {
                ws.send(JSON.stringify({
                    action: 'get_wireless_interfaces_list',
                    ip: currentSession.ip,
                    username: currentSession.username,
                    password: currentSession.password
                }));
            };
            
            ws.onmessage = function(event) {
                try {
                    const data = JSON.parse(event.data);
                    if (data.type === 'wireless_interfaces_list') {
                        const select = document.getElementById('scan-interface-select');
                        select.innerHTML = '';
                        
                        if (data.interfaces && data.interfaces.length > 0) {
                            let firstRunning = null;
                            data.interfaces.forEach(iface => {
                                const option = document.createElement('option');
                                option.value = iface.name;
                                option.textContent = iface.name;
                                if (iface.running && !firstRunning) {
                                    firstRunning = iface.name;
                                }
                                select.appendChild(option);
                            });
                            if (firstRunning) {
                                select.value = firstRunning;
                            }
                        } else {
                            select.innerHTML = '<option value="">无可用接口</option>';
                        }
                        ws.close();
                    }
                } catch (e) {
                    console.error('解析接口列表失败:', e);
                }
            };
        }
        
        function startInterferenceScanTask() {
            const select = document.getElementById('scan-interface-select');
            const interfaceName = select.value;
            const backgroundScan = document.getElementById('scan-background').checked;
            
            console.log('[干扰扫描] 选择的接口:', interfaceName, '后台扫描:', backgroundScan);
            
            if (!interfaceName) {
                alert('请选择扫描接口');
                return;
            }
            
            const startBtn = document.getElementById('scan-start-btn');
            const stopBtn = document.getElementById('scan-stop-btn');
            const resultContainer = document.getElementById('scan-result-container');
            const tbody = document.getElementById('scan-result-tbody');
            
            if (tbody) {
                tbody.innerHTML = '';
            }
            
            scanResultsData = {};
            scanSortField = 'signal_strength';
            scanSortDirection = 'desc';
            
            if (scanResultTimer) {
                clearInterval(scanResultTimer);
            }
            scanResultTimer = setInterval(renderScanResults, 1000);
            
            startBtn.disabled = true;
            startBtn.style.opacity = '0.5';
            startBtn.textContent = '扫描中';
            stopBtn.disabled = false;
            stopBtn.style.opacity = '1';
            resultContainer.style.display = 'block';
            
            const scanStats = document.getElementById('scan-stats');
            const scanCount = document.getElementById('scan-count');
            if (scanStats) scanStats.style.display = 'block';
            if (scanCount) scanCount.textContent = '0';
            
            const wsUrl = `${WS_BASE}`;
            interferenceScanWs = new WebSocket(wsUrl);
            
            interferenceScanWs.onopen = function() {
                interferenceScanWs.send(JSON.stringify({
                    action: 'start_interference_scan',
                    ip: currentSession.ip,
                    username: currentSession.username,
                    password: currentSession.password,
                    interface_name: interfaceName,
                    background: backgroundScan
                }));
            };
            
            interferenceScanWs.onmessage = function(event) {
                try {
                    const data = JSON.parse(event.data);
                    if (data.type === 'scan_result') {
                        addScanResult(data);
                    } else if (data.type === 'error') {
                        alert('扫描失败: ' + data.message);
                        stopInterferenceScanTask();
                    }
                } catch (e) {
                    console.error('解析扫描结果失败:', e);
                }
            };
            
            interferenceScanWs.onerror = function() {
                alert('连接失败');
                stopInterferenceScanTask();
            };
            
            interferenceScanWs.onclose = function() {
                interferenceScanWs = null;
            };
        }
        
        function stopInterferenceScanTask() {
            if (interferenceScanWs && interferenceScanWs.readyState === WebSocket.OPEN) {
                interferenceScanWs.send(JSON.stringify({ action: 'stop_scan' }));
                interferenceScanWs.close();
            }
            interferenceScanWs = null;
            
            if (scanResultTimer) {
                clearInterval(scanResultTimer);
                scanResultTimer = null;
            }
            
            const startBtn = document.getElementById('scan-start-btn');
            const stopBtn = document.getElementById('scan-stop-btn');
            const scanStats = document.getElementById('scan-stats');
            
            if (startBtn) {
                startBtn.disabled = false;
                startBtn.style.opacity = '1';
                startBtn.textContent = '开始';
            }
            if (stopBtn) {
                stopBtn.disabled = true;
                stopBtn.style.opacity = '0.5';
            }
            if (scanStats) {
                scanStats.style.display = 'none';
            }
        }
        
        let scanResultTimer = null;
        let scanResultsData = {};
        let scanSortField = 'signal_strength';
        let scanSortDirection = 'desc';
        
        function addScanResult(data) {
            if (!data.result) return;
            
            const address = data.result.address || '--';
            scanResultsData[address] = {
                address: address,
                ssid: data.result.ssid || '--',
                channel: data.result.channel || '--',
                signal_strength: data.result.signal_strength || '--',
                noise: data.result.noise || '--',
                snr: data.result.snr || '--'
            };
            console.log('[扫描结果] 收到数据:', address, data.result.ssid);
        }
        
        function renderScanResults() {
            const tbody = document.getElementById('scan-result-tbody');
            if (!tbody) return;
            
            const results = Object.values(scanResultsData);
            
            const scanCount = document.getElementById('scan-count');
            if (scanCount) scanCount.textContent = results.length;
            
            results.sort((a, b) => {
                let aVal = a[scanSortField] || '';
                let bVal = b[scanSortField] || '';
                
                if (scanSortField === 'signal_strength' || scanSortField === 'noise' || scanSortField === 'snr') {
                    aVal = parseInt(aVal) || -999;
                    bVal = parseInt(bVal) || -999;
                }
                
                if (aVal < bVal) return scanSortDirection === 'asc' ? -1 : 1;
                if (aVal > bVal) return scanSortDirection === 'asc' ? 1 : -1;
                return 0;
            });
            
            tbody.innerHTML = '';
            results.forEach(item => {
                const row = document.createElement('tr');
                row.innerHTML = `
                    <td>${item.address}</td>
                    <td>${item.ssid}</td>
                    <td>${item.channel}</td>
                    <td>${item.signal_strength}</td>
                    <td>${item.noise}</td>
                    <td>${item.snr}</td>
                `;
                tbody.appendChild(row);
            });
        }
        
        function sortScanResults(field) {
            if (scanSortField === field) {
                scanSortDirection = scanSortDirection === 'asc' ? 'desc' : 'asc';
            } else {
                scanSortField = field;
                scanSortDirection = 'desc';
            }
            
            updateScanTableHeader();
            renderScanResults();
        }
        
        function updateScanTableHeader() {
            const header = document.querySelector('#interference-scan-modal thead');
            if (!header) return;
            
            const sortIcon = (field) => {
                if (scanSortField !== field) return '';
                return scanSortDirection === 'asc' 
                    ? '<small class="interface-sort-hint">升序</small>' 
                    : '<small class="interface-sort-hint">降序</small>';
            };
            
            header.innerHTML = `
                <tr>
                    <th class="sortable" style="width: 140px;" onclick="sortScanResults('address')">MAC地址 ${sortIcon('address')}</th>
                    <th class="sortable" style="width: 150px;" onclick="sortScanResults('ssid')">SSID ${sortIcon('ssid')}</th>
                    <th class="sortable" style="width: 180px;" onclick="sortScanResults('channel')">信道 ${sortIcon('channel')}</th>
                    <th class="sortable" style="width: 100px;" onclick="sortScanResults('signal_strength')">信号强度 ${sortIcon('signal_strength')}</th>
                    <th class="sortable" style="width: 100px;" onclick="sortScanResults('noise')">噪声 ${sortIcon('noise')}</th>
                    <th class="sortable" style="width: 100px;" onclick="sortScanResults('snr')">信噪比 ${sortIcon('snr')}</th>
                </tr>
            `;
        }
        
        function closeInterferenceScanModal() {
            stopInterferenceScanTask();
            if (interferenceScanModal) {
                interferenceScanModal.remove();
                interferenceScanModal = null;
            }
        }
        
        let wirelessInterfacesWs = null;
        
        function connectWirelessInterfacesWebSocket() {
            if (!currentSession) return;
            
            if (wirelessInterfacesWs && wirelessInterfacesWs.readyState === WebSocket.OPEN) {
                return;
            }
            
            const wsUrl = `${WS_BASE}`;
            
            try {
                wirelessInterfacesWs = new WebSocket(wsUrl);
                
                wirelessInterfacesWs.onopen = function() {
                    console.log('无线接口WebSocket已连接');
                    wirelessInterfacesWs.send(JSON.stringify({
                        ip: currentSession.ip,
                        mac: currentSession.mac || '',
                        username: currentSession.username,
                        password: currentSession.password || '',
                        is_wireless_interfaces: true
                    }));
                };
                
                wirelessInterfacesWs.onmessage = function(event) {
                    try {
                        const data = JSON.parse(event.data);
                        if (data.type === 'wireless_interfaces') {
                            if (data.status === 'success') {
                                updateWirelessInterfacesTable(data.interfaces);
                            } else if (data.status === 'error') {
                                const tbody = document.getElementById('wireless-interfaces-tbody');
                                if (tbody) {
                                    tbody.innerHTML = `
                                        <tr>
                                            <td colspan="8" style="text-align: center; color: #e74c3c; padding: 20px;">
                                                ${data.message || '获取无线接口失败'}
                                            </td>
                                        </tr>
                                    `;
                                }
                            }
                        } else if (data.type === 'wireless_config_update') {
                            if (data.status === 'success') {
                                showNetworkAlert('操作成功', 'success');
                            } else {
                                showNetworkAlert('操作失败: ' + (data.message || '未知错误'), 'error');
                            }
                        } else if (data.type === 'error') {
                            const tbody = document.getElementById('wireless-interfaces-tbody');
                            if (tbody) {
                                tbody.innerHTML = `
                                    <tr>
                                        <td colspan="8" style="text-align: center; color: #e74c3c; padding: 20px;">
                                            ${data.message || '获取无线接口失败'}
                                        </td>
                                    </tr>
                                `;
                            }
                        }
                    } catch (e) {
                        console.error('解析无线接口数据失败:', e);
                    }
                };
                
                wirelessInterfacesWs.onerror = function(error) {
                    console.error('无线接口WebSocket错误:', error);
                };
                
                wirelessInterfacesWs.onclose = function() {
                    console.log('无线接口WebSocket已断开');
                    wirelessInterfacesWs = null;
                };
            } catch (error) {
                console.error('创建无线接口WebSocket失败:', error);
            }
        }
        
        function disconnectWirelessInterfacesWebSocket() {
            if (wirelessInterfacesWs) {
                wirelessInterfacesWs.close();
                wirelessInterfacesWs = null;
            }
            lastWirelessInterfaces = null;
            wirelessInterfacesCache = null;
            selectedWirelessInterface = null;
        }
        
        let wirelessClientsWs = null;
        
        function loadWirelessClientsContent() {
            const contentBody = document.getElementById('content-body');
            contentBody.innerHTML = `
                <div class="config-card">
                    <div class="config-card-header">
                        <h3>终端列表</h3>
                    </div>
                    <div class="config-card-body">
                        <table class="data-table">
                            <thead>
                                <tr>
                                    <th>Radio名称</th>
                                    <th>MAC地址</th>
                                    <th>接口</th>
                                    <th>连接时间</th>
                                    <th>TX/RX信号</th>
                                    <th>TX/RX信号质量</th>
                                    <th>TX/RX物理速率</th>
                                </tr>
                            </thead>
                            <tbody id="wireless-clients-tbody">
                                <tr>
                                    <td colspan="7" style="text-align: center; color: #666; padding: 20px;">
                                        加载中...
                                    </td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            `;
            connectWirelessClientsWebSocket();
        }
        
        function connectWirelessClientsWebSocket() {
            if (!currentSession) return;
            
            const wsUrl = `${WS_BASE}`;
            
            try {
                wirelessClientsWs = new WebSocket(wsUrl);
                
                wirelessClientsWs.onopen = function() {
                    console.log('[终端列表] WebSocket已连接');
                    wirelessClientsWs.send(JSON.stringify({
                        ip: currentSession.ip,
                        mac: currentSession.mac || '',
                        username: currentSession.username,
                        password: currentSession.password || '',
                        is_wireless_clients: true
                    }));
                };
                
                wirelessClientsWs.onmessage = function(event) {
                    try {
                        const data = JSON.parse(event.data);
                        if (data.type === 'wireless_clients') {
                            if (data.status === 'success' && data.clients) {
                                updateWirelessClientsTable(data.clients);
                            } else if (data.status === 'connected') {
                                console.log('[终端列表] 监控已连接');
                            } else if (data.status === 'error') {
                                console.error('[终端列表] 错误:', data.message);
                            }
                        }
                    } catch (e) {
                        console.error('[终端列表] 解析数据失败:', e);
                    }
                };
                
                wirelessClientsWs.onerror = function(error) {
                    console.error('[终端列表] WebSocket错误:', error);
                };
                
                wirelessClientsWs.onclose = function() {
                    console.log('[终端列表] WebSocket已断开');
                    wirelessClientsWs = null;
                };
            } catch (error) {
                console.error('[终端列表] 创建WebSocket失败:', error);
            }
        }
        
        function updateWirelessClientsTable(clients) {
            const tbody = document.getElementById('wireless-clients-tbody');
            if (!tbody) return;
            
            if (!clients || clients.length === 0) {
                tbody.innerHTML = `
                    <tr>
                        <td colspan="7" style="text-align: center; color: #666; padding: 20px;">
                            暂无终端连接
                        </td>
                    </tr>
                `;
                return;
            }
            
            let html = '';
            clients.forEach(c => {
                const txRxSignal = formatTxRxValue(c.tx_signal, c.rx_signal);
                const txRxQuality = formatTxRxValue(c.tx_signal_quality, c.rx_signal_quality);
                const txRxRate = formatTxRxValue(c.tx_rate, c.rx_rate);
                
                html += `
                    <tr>
                        <td>${c.interface || '--'}</td>
                        <td>${c.mac || '--'}</td>
                        <td>${c.interface || '--'}</td>
                        <td>${c.uptime || '--'}</td>
                        <td>${txRxSignal}</td>
                        <td>${txRxQuality}</td>
                        <td>${txRxRate}</td>
                    </tr>
                `;
            });
            
            tbody.innerHTML = html;
        }
        
        function formatTxRxValue(tx, rx) {
            const txVal = tx && tx !== '' ? tx : null;
            const rxVal = rx && rx !== '' ? rx : null;
            
            if (txVal && rxVal) {
                return `${txVal}/${rxVal}`;
            } else if (txVal) {
                return txVal;
            } else if (rxVal) {
                return rxVal;
            }
            return '--';
        }
        
        function disconnectWirelessClientsWebSocket() {
            if (wirelessClientsWs) {
                try {
                    if (wirelessClientsWs.readyState === WebSocket.OPEN) {
                        wirelessClientsWs.send(JSON.stringify({action: 'stop'}));
                    }
                    wirelessClientsWs.close();
                } catch (e) {
                    console.error('关闭终端列表WebSocket失败:', e);
                }
                wirelessClientsWs = null;
            }
        }
        
        let securityProfilesWs = null;
        let selectedSecurityProfile = null;
        let securityProfilesList = [];
        
        function loadSecurityProfilesContent() {
            const contentBody = document.getElementById('content-body');
            contentBody.innerHTML = `
                <div class="config-card">
                    <div class="config-card-header" style="display: flex; justify-content: space-between; align-items: center;">
                        <h3>加密配置</h3>
                        <div style="display: flex; gap: 10px;">
                            <button class="btn-disable-security" onclick="disableSecurityProfile()" disabled>禁用</button>
                            <button class="btn-enable-security" onclick="enableSecurityProfile()" disabled>启用</button>
                            <button class="btn-delete-security" onclick="deleteSecurityProfile()" disabled>删除</button>
                            <button class="btn-add-security" onclick="showAddSecurityProfilePopup()">添加</button>
                        </div>
                    </div>
                    <div class="config-card-body">
                        <table class="data-table">
                            <thead>
                                <tr>
                                    <th>名称</th>
                                    <th>加密方式</th>
                                    <th>加密算法</th>
                                    <th>密码</th>
                                </tr>
                            </thead>
                            <tbody id="security-profiles-tbody">
                                <tr>
                                    <td colspan="4" style="text-align: center; color: #666; padding: 20px;">
                                        加载中...
                                    </td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            `;
            selectedSecurityProfile = null;
            securityProfilesList = [];
            connectSecurityProfilesWebSocket();
        }
        
        function connectSecurityProfilesWebSocket() {
            if (!currentSession) return;
            
            const wsUrl = `${WS_BASE}`;
            
            try {
                securityProfilesWs = new WebSocket(wsUrl);
                
                securityProfilesWs.onopen = function() {
                    console.log('[加密配置] WebSocket已连接');
                    securityProfilesWs.send(JSON.stringify({
                        ip: currentSession.ip,
                        mac: currentSession.mac || '',
                        username: currentSession.username,
                        password: currentSession.password || '',
                        is_security_profiles: true
                    }));
                };
                
                securityProfilesWs.onmessage = function(event) {
                    try {
                        const data = JSON.parse(event.data);
                        if (data.type === 'security_profiles') {
                            if (data.status === 'success' && data.profiles) {
                                updateSecurityProfilesTable(data.profiles);
                            } else if (data.status === 'connected') {
                                console.log('[加密配置] 监控已连接');
                            } else if (data.status === 'error') {
                                console.error('[加密配置] 错误:', data.message);
                            }
                        }
                    } catch (e) {
                        console.error('[加密配置] 解析数据失败:', e);
                    }
                };
                
                securityProfilesWs.onerror = function(error) {
                    console.error('[加密配置] WebSocket错误:', error);
                };
                
                securityProfilesWs.onclose = function() {
                    console.log('[加密配置] WebSocket已断开');
                    securityProfilesWs = null;
                };
            } catch (error) {
                console.error('[加密配置] 创建WebSocket失败:', error);
            }
        }
        
        function updateSecurityProfilesTable(profiles) {
            const tbody = document.getElementById('security-profiles-tbody');
            if (!tbody) return;
            
            securityProfilesList = profiles || [];
            
            if (!profiles || profiles.length === 0) {
                tbody.innerHTML = `
                    <tr>
                        <td colspan="4" style="text-align: center; color: #666; padding: 20px;">
                            暂无加密配置
                        </td>
                    </tr>
                `;
                return;
            }
            
            let html = '';
            profiles.forEach((p, index) => {
                const isSelected = selectedSecurityProfile && selectedSecurityProfile.name === p.name;
                html += `
                    <tr onclick="selectSecurityProfile(${index})" ondblclick="showEditSecurityProfilePopup(${index})" class="${isSelected ? 'selected' : ''}" style="cursor: pointer;">
                        <td>${p.name || '--'}</td>
                        <td>${p.authentication || '--'}</td>
                        <td>${p.cipher || '--'}</td>
                        <td>${p.password || '--'}</td>
                    </tr>
                `;
            });
            
            tbody.innerHTML = html;
        }
        
        function selectSecurityProfile(index) {
            const profile = securityProfilesList[index];
            if (!profile) return;
            
            if (selectedSecurityProfile && selectedSecurityProfile.name === profile.name) {
                selectedSecurityProfile = null;
            } else {
                selectedSecurityProfile = profile;
            }
            
            updateSecurityProfilesTable(securityProfilesList);
            updateSecurityButtons();
        }
        
        function updateSecurityButtons() {
            const deleteBtn = document.querySelector('.btn-delete-security');
            const enableBtn = document.querySelector('.btn-enable-security');
            const disableBtn = document.querySelector('.btn-disable-security');
            if (deleteBtn) {
                deleteBtn.disabled = !selectedSecurityProfile;
            }
            if (enableBtn) {
                enableBtn.disabled = !selectedSecurityProfile;
            }
            if (disableBtn) {
                disableBtn.disabled = !selectedSecurityProfile;
            }
        }
        
        function showAddSecurityProfilePopup() {
            const modal = document.getElementById('security-add-modal');
            if (modal) {
                document.getElementById('security-name').value = '';
                document.getElementById('security-auth').value = 'wpa2';
                document.getElementById('security-cipher').value = 'aes';
                document.getElementById('security-password').value = '';
                modal.classList.add('active');
            }
        }
        
        function closeSecurityAddPopup() {
            const modal = document.getElementById('security-add-modal');
            if (modal) {
                modal.classList.remove('active');
            }
        }
        
        function confirmAddSecurityProfile() {
            const name = document.getElementById('security-name').value.trim();
            const auth = document.getElementById('security-auth').value;
            const cipher = document.getElementById('security-cipher').value;
            const password = document.getElementById('security-password').value;
            
            if (!name) {
                showSecurityError('请输入名称');
                return;
            }
            
            if (password.length < 8) {
                showSecurityError('密码至少需要8位');
                return;
            }
            
            let authTypes = '';
            if (auth === 'wpa') {
                authTypes = 'wpa-psk';
            } else if (auth === 'wpa2') {
                authTypes = 'wpa2-psk';
            } else if (auth === 'wpa/wpa2') {
                authTypes = 'wpa-psk,wpa2-psk';
            }
            
            let unicastCiphers = '';
            let groupCiphers = '';
            if (cipher === 'aes') {
                unicastCiphers = 'aes-ccm';
                groupCiphers = 'aes-ccm';
            } else if (cipher === 'tkip') {
                unicastCiphers = 'tkip';
                groupCiphers = 'tkip';
            } else if (cipher === 'aes/tkip') {
                unicastCiphers = 'aes-ccm,tkip';
                groupCiphers = 'aes-ccm,tkip';
            }
            
            addSecurityProfileViaApi(name, authTypes, unicastCiphers, groupCiphers, password);
        }
        
        function showSecurityError(message) {
            const errorDiv = document.getElementById('security-error-message');
            if (errorDiv) {
                errorDiv.textContent = message;
                errorDiv.style.display = 'block';
                setTimeout(() => {
                    errorDiv.style.display = 'none';
                }, 3000);
            }
        }
        
        async function addSecurityProfileViaApi(name, authTypes, unicastCiphers, groupCiphers, password) {
            try {
                const response = await fetch(`${API_BASE}/api/security-profile/add`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        ip: currentSession.ip,
                        username: currentSession.username,
                        password: currentSession.password || '',
                        name: name,
                        authTypes: authTypes,
                        unicastCiphers: unicastCiphers,
                        groupCiphers: groupCiphers,
                        wpaKey: password,
                        wpa2Key: password
                    })
                });
                
                const result = await response.json();
                
                if (result.success) {
                    closeSecurityAddPopup();
                } else {
                    showSecurityError(result.message || '添加失败');
                }
            } catch (error) {
                console.error('添加加密配置失败:', error);
                showSecurityError('添加失败: ' + error.message);
            }
        }
        
        function deleteSecurityProfile() {
            if (!selectedSecurityProfile) return;
            showConfirmModal('🗑️', '删除加密配置', '#e74c3c', `确定要删除加密配置 "${selectedSecurityProfile.name}" 吗？`, '删除', '', function() {
                confirmDeleteSecurityProfile();
            });
        }
        
        function closeSecurityDeletePopup() {
            const modal = document.getElementById('security-delete-modal');
            if (modal) {
                modal.classList.remove('active');
            }
        }
        
        async function confirmDeleteSecurityProfile() {
            if (!selectedSecurityProfile) return;
            
            try {
                const response = await fetch(`${API_BASE}/api/security-profile/delete`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        ip: currentSession.ip,
                        username: currentSession.username,
                        password: currentSession.password || '',
                        name: selectedSecurityProfile.name
                    })
                });
                
                const result = await response.json();
                
                if (result.success) {
                    selectedSecurityProfile = null;
                    updateSecurityButtons();
                } else {
                    showNetworkAlert(result.message || '删除失败', 'error');
                }
            } catch (error) {
                console.error('删除加密配置失败:', error);
                showNetworkAlert('删除失败: ' + error.message, 'error');
            }
        }
        
        function enableSecurityProfile() {
            if (!selectedSecurityProfile) return;
            showConfirmModal('✅', '启用加密配置', '#27ae60', `确定要启用加密配置 "${selectedSecurityProfile.name}" 吗？`, '启用', 'enable', function() {
                setSecurityProfileMode('dynamic-keys');
            });
        }
        
        function disableSecurityProfile() {
            if (!selectedSecurityProfile) return;
            showConfirmModal('⚠️', '禁用加密配置', '#e74c3c', `确定要禁用加密配置 "${selectedSecurityProfile.name}" 吗？禁用后无线网络将不加密。`, '禁用', '', function() {
                setSecurityProfileMode('none');
            });
        }
        
        async function setSecurityProfileMode(mode) {
            if (!selectedSecurityProfile) return;
            
            try {
                const response = await fetch(`${API_BASE}/api/security-profile/set-mode`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        ip: currentSession.ip,
                        username: currentSession.username,
                        password: currentSession.password || '',
                        name: selectedSecurityProfile.name,
                        mode: mode
                    })
                });
                
                const result = await response.json();
                
                if (!result.success) {
                    alert(result.message || '操作失败');
                }
            } catch (error) {
                console.error('设置加密配置模式失败:', error);
                alert('操作失败: ' + error.message);
            }
        }
        
        function showEditSecurityProfilePopup(index) {
            const profile = securityProfilesList[index];
            if (!profile) return;
            
            selectedSecurityProfile = profile;
            updateSecurityProfilesTable(securityProfilesList);
            updateSecurityButtons();
            
            const modal = document.getElementById('security-edit-modal');
            if (modal) {
                document.getElementById('security-edit-name').value = profile.name || '';
                
                let authValue = 'wpa2';
                if (profile.authentication === 'WPA-PSK') {
                    authValue = 'wpa';
                } else if (profile.authentication === 'WPA/WPA2-PSK') {
                    authValue = 'wpa/wpa2';
                }
                document.getElementById('security-edit-auth').value = authValue;
                
                let cipherValue = 'aes';
                if (profile.cipher === 'TKIP') {
                    cipherValue = 'tkip';
                } else if (profile.cipher === 'AES/TKIP') {
                    cipherValue = 'aes/tkip';
                }
                document.getElementById('security-edit-cipher').value = cipherValue;
                document.getElementById('security-edit-password').value = profile.password || '';
                
                modal.classList.add('active');
            }
        }
        
        function closeSecurityEditPopup() {
            const modal = document.getElementById('security-edit-modal');
            if (modal) {
                modal.classList.remove('active');
            }
        }
        
        function confirmEditSecurityProfile() {
            const name = document.getElementById('security-edit-name').value.trim();
            const auth = document.getElementById('security-edit-auth').value;
            const cipher = document.getElementById('security-edit-cipher').value;
            const password = document.getElementById('security-edit-password').value;
            
            if (!name) {
                showSecurityEditError('请输入名称');
                return;
            }
            
            if (password && password.length < 8) {
                showSecurityEditError('密码至少需要8位');
                return;
            }
            
            let authTypes = '';
            if (auth === 'wpa') {
                authTypes = 'wpa-psk';
            } else if (auth === 'wpa2') {
                authTypes = 'wpa2-psk';
            } else if (auth === 'wpa/wpa2') {
                authTypes = 'wpa-psk,wpa2-psk';
            }
            
            let unicastCiphers = '';
            let groupCiphers = '';
            if (cipher === 'aes') {
                unicastCiphers = 'aes-ccm';
                groupCiphers = 'aes-ccm';
            } else if (cipher === 'tkip') {
                unicastCiphers = 'tkip';
                groupCiphers = 'tkip';
            } else if (cipher === 'aes/tkip') {
                unicastCiphers = 'aes-ccm,tkip';
                groupCiphers = 'aes-ccm,tkip';
            }
            
            editSecurityProfileViaApi(selectedSecurityProfile.name, name, authTypes, unicastCiphers, groupCiphers, password);
        }
        
        function showSecurityEditError(message) {
            const errorDiv = document.getElementById('security-edit-error-message');
            if (errorDiv) {
                errorDiv.textContent = message;
                errorDiv.style.display = 'block';
                setTimeout(() => {
                    errorDiv.style.display = 'none';
                }, 3000);
            }
        }
        
        async function editSecurityProfileViaApi(originalName, name, authTypes, unicastCiphers, groupCiphers, password) {
            try {
                const response = await fetch(`${API_BASE}/api/security-profile/edit`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        ip: currentSession.ip,
                        username: currentSession.username,
                        password: currentSession.password || '',
                        originalName: originalName,
                        name: name,
                        authTypes: authTypes,
                        unicastCiphers: unicastCiphers,
                        groupCiphers: groupCiphers,
                        wpaKey: password,
                        wpa2Key: password
                    })
                });
                
                const result = await response.json();
                
                if (result.success) {
                    closeSecurityEditPopup();
                } else {
                    showSecurityEditError(result.message || '修改失败');
                }
            } catch (error) {
                console.error('修改加密配置失败:', error);
                showSecurityEditError('修改失败: ' + error.message);
            }
        }
        
        function disconnectSecurityProfilesWebSocket() {
            if (securityProfilesWs) {
                if (securityProfilesWs.readyState === WebSocket.OPEN) {
                    securityProfilesWs.send(JSON.stringify({action: 'stop'}));
                }
                securityProfilesWs.close();
                securityProfilesWs = null;
            }
        }
        
        function showNetworkAlert(message, type) {
            const existingModal = document.getElementById('network-alert-modal');
            if (existingModal) {
                existingModal.remove();
            }
            
            const modal = document.createElement('div');
            modal.id = 'network-alert-modal';
            modal.className = 'reconnect-modal active';
            modal.innerHTML = `
                <div class="reconnect-content">
                    <div class="reconnect-icon" style="animation: none;">${type === 'error' ? '❌' : type === 'success' ? '✅' : 'ℹ️'}</div>
                    <div class="reconnect-title" style="color: ${type === 'error' ? '#e74c3c' : type === 'success' ? '#27ae60' : '#2c3e50'};">${type === 'error' ? '错误' : type === 'success' ? '成功' : '提示'}</div>
                    <div class="reconnect-message">${message}</div>
                    <div class="interface-modal-buttons">
                        <button class="interface-confirm-btn${type === 'success' ? ' enable' : ''}" onclick="closeNetworkAlert()">确定</button>
                    </div>
                </div>
            `;
            document.body.appendChild(modal);
        }
        
        function closeNetworkAlert() {
            const modal = document.getElementById('network-alert-modal');
            if (modal) {
                modal.remove();
            }
        }
        
        function showNetworkConfirm(message, onConfirm) {
            const existingModal = document.getElementById('network-confirm-modal');
            if (existingModal) {
                existingModal.remove();
            }
            
            const modal = document.createElement('div');
            modal.id = 'network-confirm-modal';
            modal.className = 'reconnect-modal active';
            modal.innerHTML = `
                <div class="reconnect-content">
                    <div class="reconnect-icon" style="animation: none;">⚠️</div>
                    <div class="reconnect-title" style="color: #e74c3c;">确认</div>
                    <div class="reconnect-message">${message}</div>
                    <div class="interface-modal-buttons">
                        <button class="reconnect-cancel-btn" onclick="closeNetworkConfirm()">取消</button>
                        <button class="interface-confirm-btn" id="network-confirm-btn">确定</button>
                    </div>
                </div>
            `;
            document.body.appendChild(modal);
            
            document.getElementById('network-confirm-btn').onclick = function() {
                closeNetworkConfirm();
                if (onConfirm) onConfirm();
            };
        }
        
        function closeNetworkConfirm() {
            const modal = document.getElementById('network-confirm-modal');
            if (modal) {
                modal.remove();
            }
        }
        
        let networkWs = null;
        let selectedIpAddress = null;
        let ipAddressList = [];
        
        function loadNetworkContent() {
            const contentBody = document.getElementById('content-body');
            contentBody.innerHTML = `
                <div class="config-card">
                    <div class="config-card-header" style="display: flex; justify-content: space-between; align-items: center;">
                        <h3>IP地址配置</h3>
                        <div style="display: flex; gap: 10px;">
                            <button class="btn-disable-network" onclick="disableIpAddress()" disabled>禁用</button>
                            <button class="btn-enable-network" onclick="enableIpAddress()" disabled>启用</button>
                            <button class="btn-delete-network" onclick="deleteIpAddress()" disabled>删除</button>
                            <button class="btn-add-network" onclick="showAddIpAddressPopup()">添加</button>
                        </div>
                    </div>
                    <div class="config-card-body">
                        <table class="data-table">
                            <thead>
                                <tr>
                                    <th>状态</th>
                                    <th>地址</th>
                                    <th>网络</th>
                                    <th>接口</th>
                                    <th>备注</th>
                                </tr>
                            </thead>
                            <tbody id="ip-addresses-tbody">
                                <tr>
                                    <td colspan="5" style="text-align: center; color: #666; padding: 20px;">
                                        加载中...
                                    </td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            `;
            selectedIpAddress = null;
            ipAddressList = [];
            connectNetworkWebSocket();
        }
        
        function connectNetworkWebSocket() {
            if (!currentSession) return;
            
            const wsUrl = `${WS_BASE}`;
            
            try {
                networkWs = new WebSocket(wsUrl);
                
                networkWs.onopen = function() {
                    console.log('[IP地址] WebSocket已连接');
                    networkWs.send(JSON.stringify({
                        ip: currentSession.ip,
                        mac: currentSession.mac || '',
                        username: currentSession.username,
                        password: currentSession.password || '',
                        is_ip_addresses: true
                    }));
                };
                
                networkWs.onmessage = function(event) {
                    try {
                        const data = JSON.parse(event.data);
                        console.log('[IP地址] 收到消息:', data);
                        
                        if (data.type === 'ip_addresses' && data.status === 'success') {
                            ipAddressList = data.addresses || [];
                            renderIpAddressesTable();
                        } else if (data.type === 'ip_addresses' && data.status === 'error') {
                            const tbody = document.getElementById('ip-addresses-tbody');
                            if (tbody) {
                                tbody.innerHTML = `
                                    <tr>
                                        <td colspan="5" style="text-align: center; color: #e74c3c; padding: 20px;">
                                            ${data.message || '获取IP地址失败'}
                                        </td>
                                    </tr>
                                `;
                            }
                        } else if (data.type === 'ip_address_action' && data.status === 'success') {
                            console.log('[IP地址] 操作成功:', data.message);
                            showNetworkAlert(data.message || '操作成功', 'success');
                        } else if (data.type === 'ip_address_action' && data.status === 'error') {
                            showNetworkAlert(data.message || '操作失败', 'error');
                        }
                    } catch (e) {
                        console.error('[IP地址] 解析消息错误:', e);
                    }
                };
                
                networkWs.onerror = function(error) {
                    console.error('[IP地址] WebSocket错误:', error);
                };
                
                networkWs.onclose = function() {
                    console.log('[IP地址] WebSocket已关闭');
                };
                
            } catch (e) {
                console.error('[IP地址] 创建WebSocket失败:', e);
            }
        }
        
        function disconnectNetworkWebSocket() {
            if (networkWs) {
                if (networkWs.readyState === WebSocket.OPEN) {
                    networkWs.send(JSON.stringify({action: 'stop'}));
                }
                networkWs.close();
                networkWs = null;
            }
        }
        
        function renderIpAddressesTable() {
            const tbody = document.getElementById('ip-addresses-tbody');
            if (!tbody) return;
            
            if (ipAddressList.length === 0) {
                tbody.innerHTML = `
                    <tr>
                        <td colspan="5" style="text-align: center; color: #666; padding: 20px;">
                            暂无IP地址
                        </td>
                    </tr>
                `;
                return;
            }
            
            let html = '';
            ipAddressList.forEach(function(addr) {
                const isSelected = selectedIpAddress && selectedIpAddress['.id'] === addr['.id'];
                const isDynamic = addr.dynamic === 'true';
                const isDisabled = addr.disabled === 'true';
                let statusText = isDynamic ? 'DHCP' : '静态';
                let statusClass = isDynamic ? 'status-dynamic' : 'status-static';
                if (isDisabled) {
                    statusText += '(禁用)';
                    statusClass = 'status-disabled';
                }
                
                html += `
                    <tr onclick="selectIpAddress('${addr['.id']}')" 
                        ondblclick="openIpAddressEditModal('${addr['.id']}')" 
                        class="${isSelected ? 'selected' : ''}${isDisabled ? ' interface-disabled' : ''}">
                        <td><span class="status-indicator ${statusClass}"></span>${statusText}</td>
                        <td>${addr.address || '--'}</td>
                        <td>${addr.network || '--'}</td>
                        <td>${addr.interface || '--'}</td>
                        <td>${addr.name || '--'}</td>
                    </tr>
                `;
            });
            
            tbody.innerHTML = html;
            updateNetworkButtons();
        }
        
        function selectIpAddress(id) {
            selectedIpAddress = ipAddressList.find(function(addr) {
                return addr['.id'] === id;
            });
            renderIpAddressesTable();
        }
        
        function updateNetworkButtons() {
            const deleteBtn = document.querySelector('.btn-delete-network');
            const enableBtn = document.querySelector('.btn-enable-network');
            const disableBtn = document.querySelector('.btn-disable-network');
            if (deleteBtn) {
                deleteBtn.disabled = !selectedIpAddress;
            }
            if (enableBtn) {
                enableBtn.disabled = !selectedIpAddress;
            }
            if (disableBtn) {
                disableBtn.disabled = !selectedIpAddress;
            }
        }
        
        function disableIpAddress() {
            if (!selectedIpAddress || !networkWs) return;
            
            const disableIp = selectedIpAddress.address ? selectedIpAddress.address.split('/')[0] : '';
            const currentLoginIp = currentSession ? currentSession.ip : '';
            
            if (disableIp === currentLoginIp) {
                showConfirmModal('⚠️', '禁用IP地址', '#e74c3c', '检测到您正在禁用当前登录IP地址，禁用后将无法继续管理设备。确定要继续吗？', '禁用', '', function() {
                    if (networkWs && networkWs.readyState === WebSocket.OPEN) {
                        networkWs.send(JSON.stringify({
                            action: 'disable_ip_address',
                            id: selectedIpAddress['.id']
                        }));
                        
                        setTimeout(function() {
                            showNetworkAlert('登录IP地址已禁用，即将返回登录页面', 'success');
                            setTimeout(function() {
                                handleLogout();
                            }, 2000);
                        }, 1000);
                    }
                });
            } else {
                showConfirmModal('⚠️', '禁用IP地址', '#e74c3c', '确定要禁用此IP地址吗？', '禁用', '', function() {
                    if (networkWs && networkWs.readyState === WebSocket.OPEN) {
                        networkWs.send(JSON.stringify({
                            action: 'disable_ip_address',
                            id: selectedIpAddress['.id']
                        }));
                    }
                });
            }
        }
        
        function enableIpAddress() {
            if (!selectedIpAddress || !networkWs) return;
            showConfirmModal('✅', '启用IP地址', '#27ae60', '确定要启用此IP地址吗？', '启用', 'enable', function() {
                if (networkWs && networkWs.readyState === WebSocket.OPEN) {
                    networkWs.send(JSON.stringify({
                        action: 'enable_ip_address',
                        id: selectedIpAddress['.id']
                    }));
                }
            });
        }
        
        function deleteIpAddress() {
            if (!selectedIpAddress || !networkWs) return;
            
            const deleteIp = selectedIpAddress.address ? selectedIpAddress.address.split('/')[0] : '';
            const currentLoginIp = currentSession ? currentSession.ip : '';
            
            if (deleteIp === currentLoginIp) {
                showConfirmModal('🗑️', '删除IP地址', '#e74c3c', '检测到您正在删除当前登录IP地址，删除后将无法继续管理设备。确定要继续吗？', '删除', '', function() {
                    if (networkWs && networkWs.readyState === WebSocket.OPEN) {
                        networkWs.send(JSON.stringify({
                            action: 'delete_ip_address',
                            id: selectedIpAddress['.id']
                        }));
                        selectedIpAddress = null;
                        
                        setTimeout(function() {
                            showNetworkAlert('登录IP地址已删除，即将返回登录页面', 'success');
                            setTimeout(function() {
                                handleLogout();
                            }, 2000);
                        }, 1000);
                    }
                });
            } else {
                showConfirmModal('🗑️', '删除IP地址', '#e74c3c', '确定要删除此IP地址吗？', '删除', '', function() {
                    if (networkWs && networkWs.readyState === WebSocket.OPEN) {
                        networkWs.send(JSON.stringify({
                            action: 'delete_ip_address',
                            id: selectedIpAddress['.id']
                        }));
                        selectedIpAddress = null;
                    }
                });
            }
        }
        
        function showAddIpAddressPopup() {
            const modal = document.createElement('div');
            modal.id = 'ip-add-modal';
            modal.className = 'reconnect-modal active';
            modal.innerHTML = `
                <div class="wireless-config-content" style="min-width: 400px;">
                    <div class="wireless-config-header">
                        <div class="wireless-config-title">
                            <span>➕</span>
                            <span>添加IP地址</span>
                        </div>
                        <button class="detail-close-btn" onclick="closeIpAddressAddModal()">✕</button>
                    </div>
                    <div class="wireless-config-body">
                        <div class="wireless-config-form">
                            <div class="config-row">
                                <label>地址</label>
                                <input type="text" id="ip-add-address" placeholder="例如: 192.168.1.1/24">
                            </div>
                            <div class="config-row">
                                <label>接口</label>
                                <select id="ip-add-interface">
                                    <option value="">选择接口</option>
                                </select>
                            </div>
                            <div class="config-row">
                                <label>网络</label>
                                <input type="text" id="ip-add-network" placeholder="例如: 192.168.1.0">
                            </div>
                            <div class="config-row">
                                <label>备注</label>
                                <input type="text" id="ip-add-name" placeholder="可选">
                            </div>
                            <div class="config-row">
                                <label>禁用</label>
                                <span class="checkbox-container">
                                    <input type="checkbox" id="ip-add-disabled">
                                </span>
                            </div>
                        </div>
                        <div style="display: flex; justify-content: flex-end; gap: 10px; margin-top: 20px; padding-top: 15px; border-top: 1px solid #ebeef5;">
                            <button class="btn btn-primary" onclick="submitAddIpAddress()">添加</button>
                            <button class="btn btn-secondary" onclick="closeIpAddressAddModal()">取消</button>
                        </div>
                    </div>
                </div>
            `;
            document.body.appendChild(modal);
            loadInterfacesForIpAdd();
        }
        
        function loadInterfacesForIpAdd() {
            if (!currentSession) return;
            const wsUrl = `${WS_BASE}`;
            const tempWs = new WebSocket(wsUrl);
            tempWs.onopen = function() {
                tempWs.send(JSON.stringify({
                    ip: currentSession.ip,
                    mac: currentSession.mac || '',
                    username: currentSession.username,
                    password: currentSession.password || '',
                    action: 'get_interfaces_list'
                }));
            };
            tempWs.onmessage = function(event) {
                try {
                    const data = JSON.parse(event.data);
                    if (data.type === 'interfaces_list') {
                        const select = document.getElementById('ip-add-interface');
                        if (select && data.interfaces) {
                            select.innerHTML = '<option value="">选择接口</option>';
                            data.interfaces.forEach(function(iface) {
                                select.innerHTML += `<option value="${iface.name}">${iface.name}</option>`;
                            });
                        }
                    }
                } catch (e) {}
                tempWs.close();
            };
        }
        
        function closeIpAddressAddModal() {
            const modal = document.getElementById('ip-add-modal');
            if (modal) {
                modal.remove();
            }
        }
        
        function submitAddIpAddress() {
            const address = document.getElementById('ip-add-address').value.trim();
            const iface = document.getElementById('ip-add-interface').value;
            const network = document.getElementById('ip-add-network').value.trim();
            const name = document.getElementById('ip-add-name').value.trim();
            const disabled = document.getElementById('ip-add-disabled').checked;
            
            if (!address) {
                showNetworkAlert('请输入IP地址', 'error');
                return;
            }
            if (!iface) {
                showNetworkAlert('请选择接口', 'error');
                return;
            }
            
            if (networkWs && networkWs.readyState === WebSocket.OPEN) {
                networkWs.send(JSON.stringify({
                    action: 'add_ip_address',
                    address: address,
                    interface: iface,
                    network: network,
                    name: name,
                    disabled: disabled
                }));
                closeIpAddressAddModal();
            }
        }
        
        function openIpAddressEditModal(id) {
            const addr = ipAddressList.find(function(a) { return a['.id'] === id; });
            if (!addr) return;
            
            const modal = document.createElement('div');
            modal.id = 'ip-edit-modal';
            modal.className = 'reconnect-modal active';
            modal.innerHTML = `
                <div class="wireless-config-content" style="min-width: 400px;">
                    <div class="wireless-config-header">
                        <div class="wireless-config-title">
                            <span>✏️</span>
                            <span>编辑IP地址</span>
                        </div>
                        <button class="detail-close-btn" onclick="closeIpAddressEditModal()">✕</button>
                    </div>
                    <div class="wireless-config-body">
                        <div class="wireless-config-form">
                            <div class="config-row">
                                <label>地址</label>
                                <input type="text" id="ip-edit-address" value="${addr.address || ''}">
                            </div>
                            <div class="config-row">
                                <label>接口</label>
                                <select id="ip-edit-interface">
                                    <option value="">选择接口</option>
                                </select>
                            </div>
                            <div class="config-row">
                                <label>网络</label>
                                <input type="text" id="ip-edit-network" value="${addr.network || ''}">
                            </div>
                            <div class="config-row">
                                <label>备注</label>
                                <input type="text" id="ip-edit-name" value="${addr.name || ''}">
                            </div>
                            <div class="config-row">
                                <label>禁用</label>
                                <span class="checkbox-container">
                                    <input type="checkbox" id="ip-edit-disabled" ${addr.disabled === 'true' ? 'checked' : ''}>
                                </span>
                            </div>
                        </div>
                        <div style="display: flex; justify-content: flex-end; gap: 10px; margin-top: 20px; padding-top: 15px; border-top: 1px solid #ebeef5;">
                            <button class="btn btn-primary" onclick="submitEditIpAddress('${id}')">保存</button>
                            <button class="btn btn-secondary" onclick="closeIpAddressEditModal()">取消</button>
                        </div>
                    </div>
                </div>
            `;
            document.body.appendChild(modal);
            loadInterfacesForIpEdit(addr.interface);
        }
        
        function loadInterfacesForIpEdit(currentInterface) {
            if (!currentSession) return;
            const wsUrl = `${WS_BASE}`;
            const tempWs = new WebSocket(wsUrl);
            tempWs.onopen = function() {
                tempWs.send(JSON.stringify({
                    ip: currentSession.ip,
                    mac: currentSession.mac || '',
                    username: currentSession.username,
                    password: currentSession.password || '',
                    action: 'get_interfaces_list'
                }));
            };
            tempWs.onmessage = function(event) {
                try {
                    const data = JSON.parse(event.data);
                    if (data.type === 'interfaces_list') {
                        const select = document.getElementById('ip-edit-interface');
                        if (select && data.interfaces) {
                            select.innerHTML = '<option value="">选择接口</option>';
                            data.interfaces.forEach(function(iface) {
                                const selected = iface.name === currentInterface ? 'selected' : '';
                                select.innerHTML += `<option value="${iface.name}" ${selected}>${iface.name}</option>`;
                            });
                        }
                    }
                } catch (e) {}
                tempWs.close();
            };
        }
        
        function closeIpAddressEditModal() {
            const modal = document.getElementById('ip-edit-modal');
            if (modal) {
                modal.remove();
            }
        }
        
        function submitEditIpAddress(id) {
            const address = document.getElementById('ip-edit-address').value.trim();
            const iface = document.getElementById('ip-edit-interface').value;
            const network = document.getElementById('ip-edit-network').value.trim();
            const name = document.getElementById('ip-edit-name').value.trim();
            const disabled = document.getElementById('ip-edit-disabled').checked;
            
            if (!address) {
                showNetworkAlert('请输入IP地址', 'error');
                return;
            }
            if (!iface) {
                showNetworkAlert('请选择接口', 'error');
                return;
            }
            
            const newIp = address.split('/')[0];
            const currentLoginIp = currentSession ? currentSession.ip : '';
            
            if (newIp !== currentLoginIp) {
                const originalIp = selectedIpAddress ? selectedIpAddress.address.split('/')[0] : '';
                if (originalIp === currentLoginIp) {
                    showConfirmModal('⚠️', '修改IP地址', '#e74c3c', '检测到您正在修改当前登录IP地址，修改成功后需要使用新IP地址重新登录。确定要继续吗？', '确定', '', function() {
                        sendEditIpAddressRequest(id, address, iface, network, name, disabled, true);
                    });
                    return;
                }
            }
            
            sendEditIpAddressRequest(id, address, iface, network, name, disabled, false);
        }
        
        function sendEditIpAddressRequest(id, address, iface, network, name, disabled, isLoginIpChange) {
            if (networkWs && networkWs.readyState === WebSocket.OPEN) {
                networkWs.send(JSON.stringify({
                    action: 'edit_ip_address',
                    id: id,
                    address: address,
                    interface: iface,
                    network: network,
                    name: name,
                    disabled: disabled
                }));
                closeIpAddressEditModal();
                
                if (isLoginIpChange) {
                    setTimeout(function() {
                        showNetworkAlert('IP地址已修改，请使用新IP地址重新登录', 'success');
                        setTimeout(function() {
                            handleLogout();
                        }, 2000);
                    }, 1000);
                }
            }
        }
        
        let lastWirelessInterfaces = null;
        let wirelessInterfacesCache = null;
        let selectedWirelessInterface = null;
        
        function selectWirelessInterface(ifaceName, isDisabled) {
            document.querySelectorAll('#wireless-interfaces-tbody tr').forEach(function(row) {
                row.classList.remove('selected-row');
            });
            
            const selectedRow = document.querySelector(`#wireless-interfaces-tbody tr[data-interface="${ifaceName}"]`);
            if (selectedRow) {
                selectedRow.classList.add('selected-row');
                selectedWirelessInterface = { name: ifaceName, disabled: isDisabled };
            }
            
            updateWirelessInterfaceButtons();
        }
        
        function updateWirelessInterfaceButtons() {
            const enableBtn = document.getElementById('btn-enable-wireless');
            const disableBtn = document.getElementById('btn-disable-wireless');
            
            if (!enableBtn || !disableBtn) return;
            
            if (!selectedWirelessInterface) {
                enableBtn.disabled = true;
                disableBtn.disabled = true;
                enableBtn.style.opacity = '0.5';
                disableBtn.style.opacity = '0.5';
            } else {
                if (selectedWirelessInterface.disabled) {
                    enableBtn.disabled = false;
                    disableBtn.disabled = true;
                    enableBtn.style.opacity = '1';
                    disableBtn.style.opacity = '0.5';
                } else {
                    enableBtn.disabled = true;
                    disableBtn.disabled = false;
                    enableBtn.style.opacity = '0.5';
                    disableBtn.style.opacity = '1';
                }
            }
        }
        
        function handleEnableWirelessInterface() {
            if (!selectedWirelessInterface || !selectedWirelessInterface.disabled) return;
            toggleWirelessInterface(selectedWirelessInterface.name, true);
        }
        
        function handleDisableWirelessInterface() {
            if (!selectedWirelessInterface || selectedWirelessInterface.disabled) return;
            toggleWirelessInterface(selectedWirelessInterface.name, false);
        }
        
        function toggleWirelessInterface(interfaceName, isEnable) {
            showInterfaceModal(interfaceName, isEnable, function() {
                fetch(`${API_BASE}/api/interface-toggle?ip=${currentSession.ip}&interface=${encodeURIComponent(interfaceName)}&action=${isEnable ? 'enable' : 'disable'}`)
                    .then(response => response.json())
                    .then(result => {
                        if (result.status === 'success') {
                            refreshWirelessInterfacesIfActive();
                        } else {
                            showNetworkAlert('操作失败: ' + result.message, 'error');
                        }
                    })
                    .catch(error => {
                        showNetworkAlert('操作失败: ' + error.message, 'error');
                    });
            });
        }
        
        let wirelessConfigWs = null;
        let wirelessOriginalConfig = null;
        let wirelessCurrentInterfaceName = null;
        let wirelessHasAc80 = false;
        let wirelessNlevel = null;
        let wirelessNlevelLoaded = false;
        
        function updateModeOptions(nlevel) {
            const modeSelect = document.getElementById('wc-mode');
            if (!modeSelect) return;
            
            const currentValue = modeSelect.value;
            modeSelect.innerHTML = '';
            
            const modeOptions = [
                { value: 'ap-bridge', text: 'AP' },
                { value: 'station', text: '标准三层客户端' },
                { value: 'station-bridge', text: '客户端桥接' },
                { value: 'station-wds', text: '客户端桥接（WDS）' },
                { value: 'station-pseudobridge', text: '客户端对接' },
                { value: 'station-pseudobridge-clone', text: '客户端对接克隆' },
                { value: 'bridge', text: 'PTP' }
            ];
            
            modeOptions.forEach(opt => {
                if (nlevel !== null && nlevel <= 3) {
                    if (opt.value === 'ap-bridge') {
                        return;
                    }
                }
                
                if (nlevel !== null && nlevel < 4) {
                    if (opt.value === 'bridge') {
                        return;
                    }
                }
                
                const option = document.createElement('option');
                option.value = opt.value;
                option.textContent = opt.text;
                modeSelect.appendChild(option);
            });
            
            const options = modeSelect.querySelectorAll('option');
            let found = false;
            for (const opt of options) {
                if (opt.value === currentValue) {
                    modeSelect.value = currentValue;
                    found = true;
                    break;
                }
            }
            if (!found && options.length > 0) {
                modeSelect.value = options[0].value;
            }
        }
        
        function updateBandOptions(hasAc80, currentBand) {
            const bandSelect = document.getElementById('wc-band');
            if (!bandSelect) return;
            
            bandSelect.innerHTML = '';
            
            const is5GHz = currentBand && currentBand.toLowerCase().includes('5ghz');
            const is2GHz = currentBand && currentBand.toLowerCase().includes('2ghz');
            
            const bandOptions2G = [
                { value: '2ghz-b', text: '2GHz-B' },
                { value: '2ghz-onlyg', text: '2GHz-OnlyG' },
                { value: '2ghz-b/g', text: '2GHz-B/G' },
                { value: '2ghz-onlyn', text: '2GHz-OnlyN' },
                { value: '2ghz-b/g/n', text: '2GHz-B/G/N' },
                { value: '2ghz-g/n', text: '2GHz-G/N' }
            ];
            
            const bandOptions5G = [
                { value: '5ghz-a', text: '5GHz-A' },
                { value: '5ghz-a/n', text: '5GHz-A/N' },
                { value: '5ghz-onlyn', text: '5GHz-OnlyN' }
            ];
            
            const bandOptions5GAc = [
                { value: '5ghz-a', text: '5GHz-A' },
                { value: '5ghz-onlyn', text: '5GHz-OnlyN' },
                { value: '5ghz-a/n', text: '5GHz-A/N' },
                { value: '5ghz-a/n/ac', text: '5GHz-A/N/AC' },
                { value: '5ghz-onlyac', text: '5GHz-OnlyAC' },
                { value: '5ghz-n/ac', text: '5GHz-N/AC' }
            ];
            
            let bandOptions = [];
            
            if (is5GHz) {
                bandOptions = hasAc80 ? bandOptions5GAc : bandOptions5G;
            } else if (is2GHz) {
                bandOptions = bandOptions2G;
            } else {
                bandOptions = [...bandOptions2G, ...(hasAc80 ? bandOptions5GAc : bandOptions5G)];
            }
            
            bandOptions.forEach(opt => {
                const option = document.createElement('option');
                option.value = opt.value;
                option.textContent = opt.text;
                bandSelect.appendChild(option);
            });
        }
        
        function updateChannelWidthOptions(hasAc80, currentBand) {
            const channelWidthSelect = document.getElementById('wc-channel-width');
            if (!channelWidthSelect) return;
            
            channelWidthSelect.innerHTML = '';
            
            const is5GHz = currentBand && currentBand.toLowerCase().includes('5ghz');
            const is2GHz = currentBand && currentBand.toLowerCase().includes('2ghz');
            
            let widthOptions = [];
            
            if (is2GHz) {
                widthOptions = [
                    { value: '5mhz', text: '5MHz' },
                    { value: '10mhz', text: '10MHz' },
                    { value: '20mhz', text: '20MHz' },
                    { value: '20/40mhz-eC', text: '20/40MHz-eC' },
                    { value: '20/40mhz-Ce', text: '20/40MHz-Ce' }
                ];
            } else if (is5GHz && hasAc80) {
                widthOptions = [
                    { value: '5mhz', text: '5MHz' },
                    { value: '10mhz', text: '10MHz' },
                    { value: '20mhz', text: '20MHz' },
                    { value: '20/40mhz-eC', text: '20/40MHz-eC' },
                    { value: '20/40mhz-Ce', text: '20/40MHz-Ce' },
                    { value: '20/40/80mhz-Ceee', text: '20/40/80MHz-Ceee' },
                    { value: '20/40/80mhz-eCee', text: '20/40/80MHz-eCee' },
                    { value: '20/40/80mhz-eeCe', text: '20/40/80MHz-eeCe' },
                    { value: '20/40/80mhz-eeeC', text: '20/40/80MHz-eeeC' }
                ];
            } else {
                widthOptions = [
                    { value: '5mhz', text: '5MHz' },
                    { value: '10mhz', text: '10MHz' },
                    { value: '20mhz', text: '20MHz' }
                ];
            }
            
            widthOptions.forEach(opt => {
                const option = document.createElement('option');
                option.value = opt.value;
                option.textContent = opt.text;
                channelWidthSelect.appendChild(option);
            });
        }
        
        function generateFrequencyList(channelWidth, hasAc80, currentBand) {
            const frequencySelect = document.getElementById('wc-frequency');
            if (!frequencySelect) return;
            
            frequencySelect.innerHTML = '';
            
            const autoOption = document.createElement('option');
            autoOption.value = '';
            autoOption.textContent = '自动';
            frequencySelect.appendChild(autoOption);
            
            const is2GHz = currentBand && currentBand.toLowerCase().includes('2ghz');
            
            const frequencyRanges2G = {
                '20mhz': [[2412, 2462]],
                '20/40mhz-ec': [[2432, 2462]],
                '20/40mhz-ce': [[2412, 2442]]
            };
            
            const frequencyRanges5G = {
                '20mhz': [[5180, 5320], [5745, 5825]],
                '20/40mhz-ec': [[5200, 5320], [5765, 5825]],
                '20/40mhz-ce': [[5180, 5300], [5745, 5805]],
                '20/40/80mhz-ceee': [[5180, 5260], [5745, 5765]],
                '20/40/80mhz-ecee': [[5200, 5280], [5765, 5785]],
                '20/40/80mhz-eece': [[5220, 5300], [5785, 5805]],
                '20/40/80mhz-eeec': [[5240, 5320], [5805, 5825]]
            };
            
            let ranges;
            const widthLower = channelWidth.toLowerCase();
            
            if (is2GHz) {
                if (widthLower === '20/40mhz-ec') {
                    ranges = frequencyRanges2G['20/40mhz-ec'];
                } else if (widthLower === '20/40mhz-ce') {
                    ranges = frequencyRanges2G['20/40mhz-ce'];
                } else {
                    ranges = frequencyRanges2G['20mhz'];
                }
            } else {
                if (widthLower === '20/40mhz-ec') {
                    ranges = frequencyRanges5G['20/40mhz-ec'];
                } else if (widthLower === '20/40mhz-ce') {
                    ranges = frequencyRanges5G['20/40mhz-ce'];
                } else if (widthLower === '20/40/80mhz-ceee') {
                    ranges = frequencyRanges5G['20/40/80mhz-ceee'];
                } else if (widthLower === '20/40/80mhz-ecee') {
                    ranges = frequencyRanges5G['20/40/80mhz-ecee'];
                } else if (widthLower === '20/40/80mhz-eece') {
                    ranges = frequencyRanges5G['20/40/80mhz-eece'];
                } else if (widthLower === '20/40/80mhz-eeec') {
                    ranges = frequencyRanges5G['20/40/80mhz-eeec'];
                } else if (widthLower === '20/40/80mhz' || widthLower === '80mhz') {
                    ranges = frequencyRanges5G['20mhz'];
                } else {
                    ranges = frequencyRanges5G['20mhz'];
                }
            }
            
            ranges.forEach(([start, end]) => {
                for (let freq = start; freq <= end; freq += 5) {
                    const option = document.createElement('option');
                    option.value = freq;
                    if ((freq - start) % 20 === 0) {
                        option.textContent = freq;
                        option.style.fontWeight = 'bold';
                    } else {
                        option.textContent = freq;
                    }
                    frequencySelect.appendChild(option);
                }
            });
        }
        
        function openWirelessConfigModal(interfaceName) {
            if (!currentSession) {
                alert('请先登录设备');
                return;
            }
            
            wirelessCurrentInterfaceName = interfaceName;
            wirelessOriginalConfig = null;
            wirelessHasAc80 = false;
            
            const modal = document.getElementById('wireless-config-modal');
            const loadingDiv = document.getElementById('wireless-config-loading');
            const formDiv = document.getElementById('wireless-config-form');
            const nameSpan = document.getElementById('wireless-config-name');
            
            if (!modal || !loadingDiv || !formDiv) return;
            
            nameSpan.textContent = interfaceName + ' - 无线配置';
            loadingDiv.style.display = 'flex';
            loadingDiv.innerHTML = `
                <div class="loading-spinner"></div>
                <span>正在加载配置...</span>
            `;
            formDiv.style.display = 'none';
            modal.classList.add('active');
            
            if (wirelessConfigWs) {
                wirelessConfigWs.close();
                wirelessConfigWs = null;
            }
            
            var configFormShown = false;  // 标记首次加载完成，后续轮询只更新数据不重复渲染
            
            const wsUrl = `${WS_BASE}`;
            wirelessConfigWs = new WebSocket(wsUrl);
            
            wirelessConfigWs.onopen = function() {
                console.log('[无线配置] WebSocket已连接');
                wirelessConfigWs.send(JSON.stringify({
                    ip: currentSession.ip,
                    mac: currentSession.mac || '',
                    username: currentSession.username,
                    password: currentSession.password || '',
                    action: 'get_wireless_interface_config',
                    interface_name: interfaceName
                }));
            };
            
            wirelessConfigWs.onmessage = function(event) {
                try {
                    const data = JSON.parse(event.data);
                    console.log('[无线配置] 收到消息:', data);
                    
                    if (data.type === 'wireless_config') {
                        if (data.status === 'success' && data.config && data.data_complete) {
                            wirelessHasAc80 = data.has_ac || false;
                            const currentBand = data.config['band'] || '';
                            
                            if (!wirelessNlevelLoaded && data.nlevel !== undefined && data.nlevel !== null) {
                                wirelessNlevel = data.nlevel;
                                wirelessNlevelLoaded = true;
                                console.log('[无线配置] 首次获取 nlevel:', wirelessNlevel);
                            }
                            
                            console.log('[无线配置] has_ac:', wirelessHasAc80, 'band:', currentBand, 'nlevel:', wirelessNlevel);
                            
                            if (!configFormShown) {
                                // 首次数据完整才渲染表单
                                configFormShown = true;
                                updateModeOptions(wirelessNlevel);
                                updateBandOptions(wirelessHasAc80, currentBand);
                                updateChannelWidthOptions(wirelessHasAc80, currentBand);
                                
                                if (data.security_profiles && data.security_profiles.length > 0) {
                                    updateSecurityProfileOptions(data.security_profiles);
                                }
                                
                                populateWirelessConfig(data.config);
                                generateFrequencyList(data.config['channel-width'] || '20mhz', wirelessHasAc80, currentBand);
                                
                                const currentFreq = data.config['frequency'];
                                if (currentFreq) {
                                    const freqSelect = document.getElementById('wc-frequency');
                                    if (freqSelect) {
                                        const options = freqSelect.querySelectorAll('option');
                                        let found = false;
                                        for (const opt of options) {
                                            if (opt.value === currentFreq) {
                                                freqSelect.value = currentFreq;
                                                found = true;
                                                break;
                                            }
                                        }
                                        if (!found) {
                                            const newOption = document.createElement('option');
                                            newOption.value = currentFreq;
                                            newOption.textContent = currentFreq;
                                            freqSelect.appendChild(newOption);
                                            freqSelect.value = currentFreq;
                                        }
                                    }
                                }
                                
                                loadingDiv.style.display = 'none';
                                formDiv.style.display = 'grid';
                            }
                        } else if (!configFormShown && data.status !== 'success') {
                            loadingDiv.innerHTML = `
                                <div style="color: #e74c3c; text-align: center;">
                                    <span style="font-size: 24px;">⚠️</span>
                                    <p>${data.message || '获取配置失败'}</p>
                                </div>
                            `;
                        }
                    }
                } catch (e) {
                    console.error('[无线配置] 解析消息失败:', e);
                }
            };
            
            wirelessConfigWs.onerror = function(error) {
                console.error('[无线配置] WebSocket错误:', error);
                loadingDiv.innerHTML = `
                    <div style="color: #e74c3c; text-align: center;">
                        <span style="font-size: 24px;">⚠️</span>
                        <p>连接失败</p>
                    </div>
                `;
            };
            
            wirelessConfigWs.onclose = function() {
                console.log('[无线配置] WebSocket已关闭');
                wirelessConfigWs = null;
            };
        }
        
        function updateSecurityProfileOptions(profiles) {
            const select = document.getElementById('wc-security-profile');
            if (!select) return;
            
            const currentValue = select.value;
            select.innerHTML = '';
            
            profiles.forEach(profile => {
                const option = document.createElement('option');
                option.value = profile;
                option.textContent = profile;
                select.appendChild(option);
            });
            
            const options = select.querySelectorAll('option');
            let found = false;
            for (const opt of options) {
                if (opt.value === currentValue) {
                    select.value = currentValue;
                    found = true;
                    break;
                }
            }
            if (!found && options.length > 0) {
                select.value = options[0].value;
            }
        }
        
        function populateWirelessConfig(config) {
            wirelessOriginalConfig = JSON.parse(JSON.stringify(config));
            
            const fieldMap = {
                'wc-mode': 'mode',
                'wc-band': 'band',
                'wc-channel-width': 'channel-width',
                'wc-frequency': 'frequency',
                'wc-ssid': 'ssid',
                'wc-radio-name': 'radio-name',
                'wc-wireless-protocol': 'wireless-protocol',
                'wc-security-profile': 'security-profile',
                'wc-frequency-mode': 'frequency-mode',
                'wc-country': 'country'
            };
            
            for (const [elementId, configKey] of Object.entries(fieldMap)) {
                const element = document.getElementById(elementId);
                if (element && config[configKey] !== undefined) {
                    const value = config[configKey];
                    if (element.tagName === 'SELECT') {
                        const options = element.querySelectorAll('option');
                        let found = false;
                        for (const opt of options) {
                            if (opt.value === value || opt.value.toLowerCase() === value.toLowerCase()) {
                                element.value = opt.value;
                                found = true;
                                break;
                            }
                        }
                        if (!found && value) {
                            const newOption = document.createElement('option');
                            newOption.value = value;
                            newOption.textContent = value;
                            element.appendChild(newOption);
                            element.value = value;
                        }
                    } else {
                        element.value = value;
                    }
                }
            }
            
            const scanListCheckbox = document.getElementById('wc-scan-list-default');
            const scanListInput = document.getElementById('wc-scan-list');
            if (scanListCheckbox && scanListInput) {
                const scanListValue = config['scan-list'] || '';
                if (scanListValue === 'default' || scanListValue === '') {
                    scanListCheckbox.checked = true;
                    scanListInput.disabled = true;
                    scanListInput.value = '';
                } else {
                    scanListCheckbox.checked = false;
                    scanListInput.disabled = false;
                    scanListInput.value = scanListValue;
                }
            }
            
            console.log('[无线配置] 完整配置数据:', config);
            console.log('[无线配置] default-forwarding:', config['default-forwarding']);
            console.log('[无线配置] hide-ssid:', config['hide-ssid']);
            console.log('[无线配置] default-authentication:', config['default-authentication']);
            
            const defaultForwardingCheckbox = document.getElementById('wc-default-forwarding');
            const hideSsidCheckbox = document.getElementById('wc-hide-ssid');
            const defaultAuthCheckbox = document.getElementById('wc-default-authentication');
            
            if (defaultForwardingCheckbox && config['default-forwarding'] !== undefined) {
                const val = String(config['default-forwarding']).toLowerCase();
                console.log('[无线配置] 终端隔离转换后值:', val);
                defaultForwardingCheckbox.checked = val === 'true';
                console.log('[无线配置] 终端隔离勾选状态:', defaultForwardingCheckbox.checked);
            }
            if (hideSsidCheckbox && config['hide-ssid'] !== undefined) {
                const val = String(config['hide-ssid']).toLowerCase();
                console.log('[无线配置] 隐藏SSID转换后值:', val);
                hideSsidCheckbox.checked = val === 'true';
                console.log('[无线配置] 隐藏SSID勾选状态:', hideSsidCheckbox.checked);
            }
            if (defaultAuthCheckbox && config['default-authentication'] !== undefined) {
                const val = String(config['default-authentication']).toLowerCase();
                console.log('[无线配置] 默认认证转换后值:', val);
                defaultAuthCheckbox.checked = val === 'true';
                console.log('[无线配置] 默认认证勾选状态:', defaultAuthCheckbox.checked);
            }
            
            const channelWidthSelect = document.getElementById('wc-channel-width');
            if (channelWidthSelect) {
                channelWidthSelect.onchange = function() {
                    const bandSelect = document.getElementById('wc-band');
                    const currentBand = bandSelect ? bandSelect.value : '';
                    generateFrequencyList(this.value, wirelessHasAc80, currentBand);
                    validateBandAndChannelWidth();
                };
            }
            
            const bandSelect = document.getElementById('wc-band');
            if (bandSelect) {
                bandSelect.onchange = function() {
                    updateChannelWidthOptions(wirelessHasAc80, this.value);
                    const channelWidthSelect = document.getElementById('wc-channel-width');
                    const currentChannelWidth = channelWidthSelect ? channelWidthSelect.value : '20mhz';
                    generateFrequencyList(currentChannelWidth, wirelessHasAc80, this.value);
                    validateBandAndChannelWidth();
                };
            }
        }
        
        function closeWirelessConfigModal() {
            const modal = document.getElementById('wireless-config-modal');
            if (modal) {
                modal.classList.remove('active');
            }
            
            if (wirelessConfigWs) {
                try {
                    if (wirelessConfigWs.readyState === WebSocket.OPEN) {
                        wirelessConfigWs.send(JSON.stringify({action: 'close'}));
                    }
                    wirelessConfigWs.close();
                } catch (e) {
                    console.error('[无线配置] 关闭WebSocket失败:', e);
                }
                wirelessConfigWs = null;
            }
        }
        
        function showConfigErrorModal(message) {
            const modal = document.getElementById('config-error-modal');
            const messageEl = document.getElementById('config-error-message');
            const confirmBtn = document.getElementById('config-error-confirm');
            
            if (messageEl) {
                messageEl.textContent = message;
            }
            
            if (modal) {
                modal.classList.add('active');
            }
            
            if (confirmBtn) {
                confirmBtn.onclick = function() {
                    modal.classList.remove('active');
                };
            }
        }
        
        function validateBandAndChannelWidth() {
            const bandSelect = document.getElementById('wc-band');
            const channelWidthSelect = document.getElementById('wc-channel-width');
            
            if (!bandSelect || !channelWidthSelect) return true;
            
            const band = bandSelect.value.toLowerCase();
            const channelWidth = channelWidthSelect.value.toLowerCase();
            
            const bandSupports80 = band.includes('ac');
            const channelWidthRequires80 = channelWidth.includes('80');
            
            if (channelWidthRequires80 && !bandSupports80) {
                const bandText = bandSelect.options[bandSelect.selectedIndex].text;
                const channelWidthText = channelWidthSelect.options[channelWidthSelect.selectedIndex].text;
                showConfigErrorModal(`频段 ${bandText} 不支持 ${channelWidthText} 频宽，请选择包含AC的频段或更换频宽`);
                return false;
            }
            
            const is2GHz = band.includes('2ghz');
            const channelWidthRequires40 = channelWidth.includes('20/40');
            const bandSupportsN = band.includes('-n') || band.includes('/n') || band.includes('onlyn');
            
            if (is2GHz && channelWidthRequires40 && !bandSupportsN) {
                const bandText = bandSelect.options[bandSelect.selectedIndex].text;
                const channelWidthText = channelWidthSelect.options[channelWidthSelect.selectedIndex].text;
                showConfigErrorModal(`频段 ${bandText} 不支持 ${channelWidthText} 频宽，请选择包含N协议的频段或更换频宽`);
                return false;
            }
            
            return true;
        }
        
        function toggleScanListInput() {
            const checkbox = document.getElementById('wc-scan-list-default');
            const scanListInput = document.getElementById('wc-scan-list');
            if (checkbox && scanListInput) {
                if (checkbox.checked) {
                    scanListInput.disabled = true;
                    scanListInput.value = '';
                } else {
                    scanListInput.disabled = false;
                }
            }
        }
        
        function validateScanList() {
            const checkbox = document.getElementById('wc-scan-list-default');
            const scanListInput = document.getElementById('wc-scan-list');
            
            if (checkbox && checkbox.checked) {
                return true;
            }
            
            if (!scanListInput) return true;
            
            const value = scanListInput.value.trim();
            
            if (!value) {
                return true;
            }
            
            const commaPattern = /^\d{4}(-\d{4})?(,\d{4}(-\d{4})?)*$/;
            const rangePattern = /^\d{4}-\d{4}$/;
            
            if (commaPattern.test(value) || rangePattern.test(value)) {
                return true;
            }
            
            showConfigErrorModal('频率绑定格式不正确，支持格式：\n1. 单个频率\n2. 多个频率（英文逗号分隔）\n3. 频率范围（短横线连接）\n4. 组合格式');
            return false;
        }
        
        function saveWirelessConfig() {
            if (!wirelessOriginalConfig || !wirelessCurrentInterfaceName) {
                closeWirelessConfigModal();
                return;
            }
            
            if (!validateBandAndChannelWidth()) {
                return;
            }
            
            if (!validateScanList()) {
                return;
            }
            
            const configChanges = {};
            
            const fieldMap = {
                'wc-mode': 'mode',
                'wc-band': 'band',
                'wc-channel-width': 'channel-width',
                'wc-frequency': 'frequency',
                'wc-ssid': 'ssid',
                'wc-radio-name': 'radio-name',
                'wc-wireless-protocol': 'wireless-protocol',
                'wc-security-profile': 'security-profile',
                'wc-frequency-mode': 'frequency-mode',
                'wc-country': 'country'
            };
            
            for (const [elementId, configKey] of Object.entries(fieldMap)) {
                const element = document.getElementById(elementId);
                if (element && wirelessOriginalConfig[configKey] !== undefined) {
                    const originalVal = wirelessOriginalConfig[configKey] || '';
                    const currentVal = element.value || '';
                    if (originalVal !== currentVal) {
                        configChanges[configKey] = currentVal;
                    }
                }
            }
            
            const scanListCheckbox = document.getElementById('wc-scan-list-default');
            const scanListInput = document.getElementById('wc-scan-list');
            if (wirelessOriginalConfig['scan-list'] !== undefined) {
                const originalVal = wirelessOriginalConfig['scan-list'] || '';
                let currentVal;
                if (scanListCheckbox && scanListCheckbox.checked) {
                    currentVal = 'default';
                } else if (scanListInput) {
                    currentVal = scanListInput.value || '';
                }
                if (originalVal !== currentVal) {
                    configChanges['scan-list'] = currentVal;
                }
            }
            
            const defaultForwardingCheckbox = document.getElementById('wc-default-forwarding');
            const hideSsidCheckbox = document.getElementById('wc-hide-ssid');
            const defaultAuthCheckbox = document.getElementById('wc-default-authentication');
            
            if (defaultForwardingCheckbox && wirelessOriginalConfig['default-forwarding'] !== undefined) {
                const originalVal = String(wirelessOriginalConfig['default-forwarding']).toLowerCase() === 'true';
                const currentVal = defaultForwardingCheckbox.checked;
                if (originalVal !== currentVal) {
                    configChanges['default-forwarding'] = currentVal ? 'yes' : 'no';
                }
            }
            if (hideSsidCheckbox && wirelessOriginalConfig['hide-ssid'] !== undefined) {
                const originalVal = String(wirelessOriginalConfig['hide-ssid']).toLowerCase() === 'true';
                const currentVal = hideSsidCheckbox.checked;
                if (originalVal !== currentVal) {
                    configChanges['hide-ssid'] = currentVal ? 'yes' : 'no';
                }
            }
            if (defaultAuthCheckbox && wirelessOriginalConfig['default-authentication'] !== undefined) {
                const originalVal = String(wirelessOriginalConfig['default-authentication']).toLowerCase() === 'true';
                const currentVal = defaultAuthCheckbox.checked;
                if (originalVal !== currentVal) {
                    configChanges['default-authentication'] = currentVal ? 'yes' : 'no';
                }
            }
            
            console.log('[无线配置] 原始配置:', wirelessOriginalConfig);
            console.log('[无线配置] 变更配置:', configChanges);
            
            if (Object.keys(configChanges).length === 0) {
                closeWirelessConfigModal();
                return;
            }
            
            const loadingDiv = document.getElementById('wireless-config-loading');
            const formDiv = document.getElementById('wireless-config-form');
            
            if (loadingDiv && formDiv) {
                loadingDiv.innerHTML = `
                    <div class="loading-spinner"></div>
                    <span>正在保存配置...</span>
                `;
                loadingDiv.style.display = 'flex';
                formDiv.style.display = 'none';
            }
            
            const wsUrl = `${WS_BASE}`;
            const updateWs = new WebSocket(wsUrl);
            
            updateWs.onopen = function() {
                console.log('[无线配置更新] WebSocket已连接');
                updateWs.send(JSON.stringify({
                    ip: currentSession.ip,
                    mac: currentSession.mac || '',
                    username: currentSession.username,
                    password: currentSession.password || '',
                    action: 'set_wireless_interface_config',
                    interface_name: wirelessCurrentInterfaceName,
                    config_changes: configChanges
                }));
            };
            
            updateWs.onmessage = function(event) {
                try {
                    const data = JSON.parse(event.data);
                    console.log('[无线配置更新] 收到消息:', data);
                    
                    if (data.type === 'wireless_config_update') {
                        updateWs.close();
                        closeWirelessConfigModal();
                    }
                } catch (e) {
                    console.error('[无线配置更新] 解析消息失败:', e);
                }
            };
            
            updateWs.onerror = function(error) {
                console.error('[无线配置更新] WebSocket错误:', error);
                updateWs.close();
                closeWirelessConfigModal();
            };
        }
        
        function getModeText(mode) {
            const modeMap = {
                'ap-bridge': 'AP',
                'station': '标准三层客户端',
                'station-bridge': '客户端桥接',
                'station-wds': '客户端桥接（WDS）',
                'station-pseudobridge': '客户端对接',
                'station-pseudobridge-clone': '客户端对接克隆',
                'wds-slave': 'WDS从站',
                'bridge': 'PTP',
                'alignment-only': '仅对齐',
                'nstreme-dual-slave': 'Nstreme双从站',
                'ptp': 'PTP',
                'ptp-pmp': 'PTP',
                'nstreme': 'Nstreme',
                'nv2': 'NV2'
            };
            return modeMap[mode] || mode || '--';
        }
        
        function updateWirelessInterfacesTable(interfaces) {
            const tbody = document.getElementById('wireless-interfaces-tbody');
            if (!tbody) return;
            
            if (!interfaces || interfaces.length === 0) {
                if (wirelessInterfacesCache && wirelessInterfacesCache.length > 0) {
                    console.log('[无线接口] 获取到空列表，使用缓存数据（可能是网络问题）');
                    renderWirelessInterfaces(wirelessInterfacesCache, tbody);
                    return;
                }
                tbody.innerHTML = `
                    <tr>
                        <td colspan="8" style="text-align: center; color: #666; padding: 20px;">
                            暂无无线接口数据
                        </td>
                    </tr>
                `;
                lastWirelessInterfaces = null;
                selectedWirelessInterface = null;
                return;
            }
            
            if (wirelessInterfacesCache && interfaces.length < wirelessInterfacesCache.length) {
                console.log(`[无线接口] 接口数量减少 (${interfaces.length} < ${wirelessInterfacesCache.length})，使用缓存数据（可能是网络问题）`);
                renderWirelessInterfaces(wirelessInterfacesCache, tbody);
                return;
            }
            
            wirelessInterfacesCache = interfaces;
            lastWirelessInterfaces = interfaces;
            renderWirelessInterfaces(interfaces, tbody);
        }
        
        function renderWirelessInterfaces(interfaces, tbody) {
            let tableHtml = '';
            
            interfaces.forEach(iface => {
                const rowClass = iface.disabled ? 'interface-disabled' : '';
                tableHtml += `
                    <tr data-interface="${iface.name}" class="${rowClass}" onclick="selectWirelessInterface('${iface.name}', ${iface.disabled})" ondblclick="openWirelessConfigModal('${iface.name}')">
                        <td><span class="status-indicator ${iface.disabled ? 'status-disabled' : (iface.running ? 'running' : 'stopped')}"></span><span class="status-text">${iface.disabled ? '禁用' : (iface.running ? '运行' : '停止')}</span></td>
                        <td class="col-name">${iface.name || '--'}</td>
                        <td class="col-mode">${getModeText(iface.mode)}</td>
                        <td class="col-ssid">${iface.ssid || '--'}</td>
                        <td class="col-frequency">${iface.frequency || '--'}</td>
                        <td class="col-band">${iface.band || '--'}</td>
                        <td class="col-channel-width">${iface.channel_width || '--'}</td>
                        <td class="col-protocol">${iface.protocol || '--'}</td>
                    </tr>
                `;
            });
            
            tbody.innerHTML = tableHtml;
            
            if (selectedWirelessInterface) {
                const selectedRow = tbody.querySelector(`tr[data-interface="${selectedWirelessInterface.name}"]`);
                if (selectedRow) {
                    selectedRow.classList.add('selected-row');
                }
            }
        }
        
        let deviceInfoInterval = null;
        let deviceCpuInterval = null;
        let deviceTimeSyncInterval = null;
        let localTimeUpdateInterval = null;
        let deviceTimeData = {
            date: '',
            time: '',
            localTimestamp: null
        };
        let deviceUptimeData = {
            bootTime: null,
            initialUptime: null
        };
        let contentLoadTime = null;

        function clearDeviceTimeCache() {
            deviceTimeData = { date: '', time: '', localTimestamp: null };
            deviceUptimeData = { initialUptime: null, localTimestamp: null };
            contentLoadTime = null;
        }

        function parseDeviceTime(timeStr) {
            const parts = timeStr.split(':');
            if (parts.length >= 3) {
                return {
                    hours: parseInt(parts[0], 10),
                    minutes: parseInt(parts[1], 10),
                    seconds: parseInt(parts[2].split('.')[0], 10)
                };
            }
            return null;
        }
        
        function parseUptime(uptime) {
            if (!uptime) return null;
            const weeks = parseInt((uptime.match(/(\d+)w/) || ['', '0'])[1]) || 0;
            const days = parseInt((uptime.match(/(\d+)d/) || ['', '0'])[1]) || 0;
            const hours = parseInt((uptime.match(/(\d+)h/) || ['', '0'])[1]) || 0;
            const minutes = parseInt((uptime.match(/(\d+)m/) || ['', '0'])[1]) || 0;
            const seconds = parseInt((uptime.match(/(\d+)s/) || ['', '0'])[1]) || 0;
            return weeks * 604800 + days * 86400 + hours * 3600 + minutes * 60 + seconds;
        }
        
        function formatUptimeFromSeconds(totalSeconds) {
            if (totalSeconds === null) return '--';
            const weeks = Math.floor(totalSeconds / 604800);
            const days = Math.floor((totalSeconds % 604800) / 86400);
            const hours = Math.floor((totalSeconds % 86400) / 3600);
            const minutes = Math.floor((totalSeconds % 3600) / 60);
            const seconds = totalSeconds % 60;
            
            let result = '';
            if (weeks > 0) result += weeks + '周';
            if (days > 0) result += days + '天';
            if (hours > 0) result += hours + '时';
            if (minutes > 0) result += minutes + '分';
            if (seconds > 0 || result === '') result += seconds + '秒';
            return result;
        }
        
        function formatLocalTime(date, time) {
            if (!date || !time) return '--';
            return date + ' ' + time;
        }
        
        function updateLocalTime() {
            if (!deviceTimeData.date || !deviceTimeData.time || !deviceTimeData.localTimestamp) {
                return;
            }

            if (deviceTimeData.localTimestamp < contentLoadTime) {
                return;
            }

            const elapsed = Math.floor((Date.now() - deviceTimeData.localTimestamp) / 1000);

            const parsed = parseDeviceTime(deviceTimeData.time);
            if (!parsed) return;

            let totalSeconds = parsed.hours * 3600 + parsed.minutes * 60 + parsed.seconds + elapsed;

            const hours = Math.floor(totalSeconds / 3600) % 24;
            const minutes = Math.floor((totalSeconds % 3600) / 60);
            const seconds = totalSeconds % 60;

            const newTime = [
                hours.toString().padStart(2, '0'),
                minutes.toString().padStart(2, '0'),
                seconds.toString().padStart(2, '0')
            ].join(':');

            const timeEl = document.getElementById('device-time');
            if (timeEl) {
                timeEl.textContent = formatLocalTime(deviceTimeData.date, newTime);
            }

            if (deviceUptimeData.localTimestamp !== null && deviceUptimeData.localTimestamp >= contentLoadTime) {
                const uptimeElapsed = Math.floor((Date.now() - deviceUptimeData.localTimestamp) / 1000);
                const currentUptime = deviceUptimeData.initialUptime + uptimeElapsed;
                const uptimeEl = document.getElementById('uptime-value');
                if (uptimeEl) {
                    uptimeEl.textContent = formatUptimeFromSeconds(currentUptime);
                }
            }
        }
        
        async function loadDeviceInfoContent() {
            if (deviceInfoInterval) {
                clearInterval(deviceInfoInterval);
            }
            if (deviceTimeSyncInterval) {
                clearInterval(deviceTimeSyncInterval);
            }
            if (localTimeUpdateInterval) {
                clearInterval(localTimeUpdateInterval);
            }
            if (deviceCpuInterval) {
                clearInterval(deviceCpuInterval);
            }

            contentLoadTime = Date.now();
            clearDeviceTimeCache();
            
            document.getElementById('content-body').innerHTML = `
                <div class="device-info-container">
                    <div class="device-info-grid">
                        <div class="info-card">
                            <div class="info-icon">
                                <svg viewBox="0 0 24 24"><path d="M11.99 2C6.47 2 2 6.48 2 12s4.47 10 9.99 10C17.52 22 22 17.52 22 12S17.52 2 11.99 2zM12 20c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8zm.5-13H11v6l5.25 3.15.75-1.23-4.5-2.67z"/></svg>
                            </div>
                            <div class="info-content">
                                <div class="info-label">系统时间</div>
                                <div class="info-value" id="device-time">--</div>
                            </div>
                        </div>
                        <div class="info-card">
                            <div class="info-icon cpu">
                                <svg viewBox="0 0 24 24"><path d="M15 9H9v6h6V9zm-2 4h-2v-2h2v2zm8-2V9h-2V7c0-1.1-.9-2-2-2h-2V3h-2v2h-2V3H9v2H7c-1.1 0-2 .9-2 2v2H3v2h2v2H3v2h2v2c0 1.1.9 2 2 2h2v2h2v-2h2v2h2v-2h2c1.1 0 2-.9 2-2v-2h2v-2h-2v-2h2zm-4 6H7V7h10v10z"/></svg>
                            </div>
                            <div class="info-content">
                                <div class="info-label">CPU 使用率</div>
                                <div class="info-value" id="device-cpu">--</div>
                                <div class="cpu-bar">
                                    <div class="cpu-bar-fill" id="cpu-bar-fill"></div>
                                </div>
                            </div>
                        </div>
                        <div class="info-card">
                            <div class="info-icon version">
                                <svg viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 17.93c-3.95-.49-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39z"/></svg>
                            </div>
                            <div class="info-content">
                                <div class="info-label">系统版本</div>
                                <div class="info-value" id="device-version">--</div>
                            </div>
                        </div>
                        <div class="info-card">
                            <div class="info-icon voltage">
                                <svg viewBox="0 0 24 24"><path d="M11 21h-1l1-7H7.5c-.58 0-.57-.32-.38-.66.19-.34.05-.08.07-.12C8.48 10.94 10.42 7.54 13 3h1l-1 7h3.5c.49 0 .56.33.47.51l-.07.15C12.96 17.55 11 21 11 21z"/></svg>
                            </div>
                            <div class="info-content">
                                <div class="info-label">系统电压</div>
                                <div class="info-value" id="device-voltage">--</div>
                            </div>
                        </div>
                    </div>
                    <div class="device-info-details">
                        <div class="config-card">
                            <div class="config-card-header">
                                <h3>系统详情</h3>
                            </div>
                            <div class="config-card-body">
                                <div class="detail-grid" id="device-details">
                                    <div class="loading-spinner">
                                        <svg viewBox="0 0 24 24"><path d="M12 4V1L8 5l4 4V6c3.31 0 6 2.69 6 6 0 1.01-.25 1.97-.7 2.8l1.46 1.46C19.54 15.03 20 13.57 20 12c0-4.42-3.58-8-8-8zm0 14c-3.31 0-6-2.69-6-6 0-1.01.25-1.97.7-2.8L5.24 7.74C4.46 8.97 4 10.43 4 12c0 4.42 3.58 8 8 8v3l4-4-4-4v3z"/></svg>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            `;
            
            fetchDeviceInfo(true).then(() => {
                deviceInfoInterval = setInterval(() => fetchDeviceInfo(false), 5000);
                deviceCpuInterval = setInterval(() => fetchCpuLoad(), 1000);
                deviceTimeSyncInterval = setInterval(() => fetchDeviceInfo(true), 120000);
                localTimeUpdateInterval = setInterval(updateLocalTime, 1000);
            });
        }
        
        async function fetchDeviceInfo(syncTime = false) {
            if (!currentSession) return;
            
            try {
                const response = await fetch(`${API_BASE}/api/device-info?ip=${encodeURIComponent(currentSession.ip)}`, {
                    method: 'GET',
                    headers: { 'Content-Type': 'application/json' }
                });
                
                const data = await response.json();
                
                if (data.status === 'success') {
                    const info = data.info;
                    
                    if (syncTime || !deviceTimeData.localTimestamp) {
                        deviceTimeData.date = info.date || '';
                        deviceTimeData.time = info.device_time || '';
                        deviceTimeData.localTimestamp = Date.now();
                        updateLocalTime();
                    }

                    if (syncTime) {
                        deviceUptimeData.initialUptime = parseUptime(info.uptime);
                        deviceUptimeData.localTimestamp = Date.now();
                        const uptimeEl = document.getElementById('uptime-value');
                        if (uptimeEl) {
                            uptimeEl.textContent = formatUptimeFromSeconds(deviceUptimeData.initialUptime);
                        }
                    }

                    const cpuLoad = info.cpu_load || '0';
                    const cpuEl = document.getElementById('device-cpu');
                    const cpuBarEl = document.getElementById('cpu-bar-fill');
                    const versionEl = document.getElementById('device-version');
                    const voltageEl = document.getElementById('device-voltage');
                    if (cpuEl) cpuEl.textContent = cpuLoad + '%';
                    if (cpuBarEl) cpuBarEl.style.width = cpuLoad + '%';
                    if (versionEl) versionEl.textContent = info.version || '--';
                    if (voltageEl) voltageEl.textContent = info.voltage || '--';

                    if (syncTime) {
                        const detailsHtml = `
                            <div class="detail-item"><span class="detail-label">设备名称</span><span class="detail-value">${info.identity || '--'}</span></div>
                            <div class="detail-item"><span class="detail-label">运行时间</span><span class="detail-value" id="uptime-value">${formatUptimeFromSeconds(parseUptime(info.uptime))}</span></div>
                            <div class="detail-item"><span class="detail-label">CPU 架构</span><span class="detail-value">${info.cpu || '--'} ${info.cpu_count ? '(' + info.cpu_count + '核)' : ''}</span></div>
                            <div class="detail-item"><span class="detail-label">CPU 频率</span><span class="detail-value">${info.cpu_frequency || '--'}</span></div>
                            <div class="detail-item"><span class="detail-label">内存使用</span><span class="detail-value">${info.memory_used || '--'} / ${info.memory_total || '--'}</span></div>
                            <div class="detail-item"><span class="detail-label">存储使用</span><span class="detail-value">${info.hdd_used || '--'} / ${info.hdd_total || '--'}</span></div>
                            <div class="detail-item"><span class="detail-label">架构</span><span class="detail-value">${info.architecture || '--'}</span></div>
                        `;
                        const deviceDetailsEl = document.getElementById('device-details');
                        if (deviceDetailsEl) {
                            deviceDetailsEl.innerHTML = detailsHtml;
                        }
                    }
                }
            } catch (error) {
                console.error('获取设备信息失败:', error);
            }
        }

        async function fetchCpuLoad() {
            if (!currentSession) return;
            try {
                const response = await fetch(`${API_BASE}/api/cpu-usage?ip=${encodeURIComponent(currentSession.ip)}`, {
                    method: 'GET',
                    headers: { 'Content-Type': 'application/json' }
                });
                const data = await response.json();
                if (data.status === 'success') {
                    const cpuLoad = data.cpu_usage ? data.cpu_usage.replace('%', '') : '0';
                    const cpuEl = document.getElementById('device-cpu');
                    const cpuBarEl = document.getElementById('cpu-bar-fill');
                    if (cpuEl) cpuEl.textContent = cpuLoad + '%';
                    if (cpuBarEl) cpuBarEl.style.width = cpuLoad + '%';
                }
            } catch (error) {
            }
        }
        
        async function loadInterfacesContent() {
            document.getElementById('content-body').innerHTML = `
                <div class="config-card">
                    <div class="config-card-header">
                        <h3>接口列表</h3>
                        <button class="btn btn-refresh" onclick="refreshInterfaces()">刷新</button>
                    </div>
                    <div class="config-card-body">
                        <div class="loading-spinner">
                            <svg viewBox="0 0 24 24"><path d="M12 4V1L8 5l4 4V6c3.31 0 6 2.69 6 6 0 1.01-.25 1.97-.7 2.8l1.46 1.46C19.54 15.03 20 13.57 20 12c0-4.42-3.58-8-8-8zm0 14c-3.31 0-6-2.69-6-6 0-1.01.25-1.97.7-2.8L5.24 7.74C4.46 8.97 4 10.43 4 12c0 4.42 3.58 8 8 8v3l4-4-4-4v3z"/></svg>
                        </div>
                    </div>
                </div>
            `;
            
            connectInterfaceWebSocket();
        }
        
        async function fetchInterfaces() {
            if (!currentSession) return;
            
            try {
                const response = await fetch(`${API_BASE}/api/interfaces?ip=${currentSession.ip}`);
                const result = await response.json();
                
                if (result.status === 'success') {
                    renderInterfaces(result.interfaces);
                } else {
                    document.getElementById('content-body').innerHTML = `
                        <div class="config-card">
                            <div class="config-card-header">
                                <h3>接口列表</h3>
                                <button class="btn btn-refresh" onclick="refreshInterfaces()">刷新</button>
                            </div>
                            <div class="config-card-body">
                                <p style="color: #f44336; text-align: center; padding: 40px;">
                                    ${result.message}
                                </p>
                            </div>
                        </div>
                    `;
                }
            } catch (error) {
                document.getElementById('content-body').innerHTML = `
                    <div class="config-card">
                        <div class="config-card-header">
                            <h3>接口列表</h3>
                            <button class="btn btn-refresh" onclick="refreshInterfaces()">刷新</button>
                        </div>
                        <div class="config-card-body">
                            <p style="color: #f44336; text-align: center; padding: 40px;">
                                获取接口列表失败: ${error.message}
                            </p>
                        </div>
                    </div>
                `;
            }
        }
        
        let currentInterfaces = [];
        let interfaceWs = null;
        let interfaceCache = {};
        let interfaceMissCount = {};
        const MAX_MISS_COUNT = 3;

        function mergeInterfacesWithCache(newInterfaces) {
            const now = Date.now();
            const result = [];
            const newInterfaceNames = new Set(newInterfaces.map(i => i.name));
            
            for (const iface of newInterfaces) {
                interfaceCache[iface.name] = {
                    data: iface,
                    lastUpdate: now
                };
                if (interfaceMissCount[iface.name]) {
                    delete interfaceMissCount[iface.name];
                }
                result.push(iface);
            }
            
            for (const [name, cached] of Object.entries(interfaceCache)) {
                if (!newInterfaceNames.has(name)) {
                    if (!interfaceMissCount[name]) {
                        interfaceMissCount[name] = 0;
                    }
                    interfaceMissCount[name]++;
                    
                    if (interfaceMissCount[name] < MAX_MISS_COUNT) {
                        result.push(cached.data);
                    } else {
                        delete interfaceCache[name];
                        delete interfaceMissCount[name];
                    }
                }
            }
            
            return result;
        }

        function connectInterfaceWebSocket() {
            if (!currentSession) return;
            
            if (interfaceWs && interfaceWs.readyState === WebSocket.OPEN) {
                return;
            }
            
            const wsUrl = `${WS_BASE}`;
            interfaceWs = new WebSocket(wsUrl);
            
            interfaceWs.onopen = function() {
                console.log('接口列表WebSocket已连接');
                interfaceWs.send(JSON.stringify({
                    ip: currentSession.ip,
                    mac: currentSession.mac || '',
                    username: currentSession.username,
                    password: currentSession.password || '',
                    is_interface_polling: true
                }));
            };
            
            interfaceWs.onmessage = function(event) {
                try {
                    const data = JSON.parse(event.data);
                    if (data.type === 'interface_list') {
                        if (data.status === 'success' && data.interfaces) {
                            renderInterfaces(data.interfaces);
                            updateTrafficInterfaces(data.interfaces);
                        } else if (data.status === 'error') {
                            console.error('接口列表错误:', data.message);
                        } else if (data.status === 'device_offline') {
                            console.log('接口列表检测到设备离线');
                            if (!isLoggingOut && !reconnectTimer) {
                                showReconnectModal();
                                reconnectWebSocket();
                            }
                        }
                    } else if (data.type === 'interface_traffic') {
                        if (data.status === 'success' && data.traffic) {
                            updateTrafficDisplay(data.traffic);
                        }
                    }
                } catch (e) {
                    console.error('解析接口列表消息失败:', e);
                }
            };
            
            interfaceWs.onclose = function() {
                console.log('接口列表WebSocket已断开');
                interfaceWs = null;
            };
            
            interfaceWs.onerror = function(error) {
                console.error('接口列表WebSocket错误:', error);
            };
        }

        function disconnectInterfaceWebSocket() {
            if (interfaceWs) {
                try {
                    if (interfaceWs.readyState === WebSocket.OPEN) {
                        interfaceWs.send(JSON.stringify({action: 'stop'}));
                    }
                    interfaceWs.close();
                } catch (e) {
                    console.error('关闭接口列表WebSocket失败:', e);
                }
                interfaceWs = null;
            }
        }

        function updateTrafficInterfaces(interfaces) {
            currentInterfaces = interfaces || [];
        }

        let lastTrafficData = {};
        let pendingTrafficData = {};
        let trafficDisplayTimer = null;

        function formatBitrate(bps) {
            let kbps = bps / 1000;
            return kbps.toFixed(2) + ' Kbps';
        }

        function startTrafficDisplayTimer() {
            if (trafficDisplayTimer) return;
            trafficDisplayTimer = setInterval(function() {
                if (Object.keys(pendingTrafficData).length > 0) {
                    for (const [ifaceName, traffic] of Object.entries(pendingTrafficData)) {
                        const safeName = ifaceName.replace(/[^a-zA-Z0-9_-]/g, '_');
                        const txElement = document.getElementById(`tx-${safeName}`);
                        const rxElement = document.getElementById(`rx-${safeName}`);
                        
                        if (txElement && traffic.tx_bps !== undefined) {
                            txElement.textContent = formatBitrate(traffic.tx_bps);
                        }
                        if (rxElement && traffic.rx_bps !== undefined) {
                            rxElement.textContent = formatBitrate(traffic.rx_bps);
                        }
                        
                        lastTrafficData[ifaceName] = { tx_bps: traffic.tx_bps, rx_bps: traffic.rx_bps };
                    }
                    pendingTrafficData = {};
                }
            }, 500);
        }

        function updateTrafficDisplay(trafficData) {
            for (const [ifaceName, traffic] of Object.entries(trafficData)) {
                pendingTrafficData[ifaceName] = { tx_bps: traffic.tx_bps, rx_bps: traffic.rx_bps };
            }
            startTrafficDisplayTimer();
        }

        let selectedInterface = null;

        function selectInterface(ifaceName, isDisabled) {
            document.querySelectorAll('#interface-table-body tr').forEach(function(row) {
                row.classList.remove('selected-row');
            });
            
            const selectedRow = document.querySelector(`tr[data-interface="${ifaceName}"]`);
            if (selectedRow) {
                selectedRow.classList.add('selected-row');
                selectedInterface = { name: ifaceName, disabled: isDisabled };
            }
            
            updateInterfaceButtons();
        }

        function updateInterfaceButtons() {
            const enableBtn = document.getElementById('btn-enable-interface');
            const disableBtn = document.getElementById('btn-disable-interface');
            
            if (!enableBtn || !disableBtn) return;
            
            if (!selectedInterface) {
                enableBtn.disabled = true;
                disableBtn.disabled = true;
                enableBtn.style.opacity = '0.5';
                disableBtn.style.opacity = '0.5';
            } else {
                if (selectedInterface.disabled) {
                    enableBtn.disabled = false;
                    disableBtn.disabled = true;
                    enableBtn.style.opacity = '1';
                    disableBtn.style.opacity = '0.5';
                } else {
                    enableBtn.disabled = true;
                    disableBtn.disabled = false;
                    enableBtn.style.opacity = '0.5';
                    disableBtn.style.opacity = '1';
                }
            }
        }

        function handleEnableInterface() {
            if (!selectedInterface || !selectedInterface.disabled) return;
            toggleInterface(selectedInterface.name, true);
        }

        function handleDisableInterface() {
            if (!selectedInterface || selectedInterface.disabled) return;
            toggleInterface(selectedInterface.name, false);
        }

        let interfaceSortField = null;
        let interfaceSortDirection = 'asc';

        function sortInterfaces(field) {
            if (interfaceSortField === field) {
                interfaceSortDirection = interfaceSortDirection === 'asc' ? 'desc' : 'asc';
            } else {
                interfaceSortField = field;
                interfaceSortDirection = 'asc';
            }
            
            renderInterfaces(currentInterfaces);
        }

        function getSortedInterfaces(interfaces) {
            if (!interfaceSortField || !interfaces) return interfaces;
            
            const sorted = [...interfaces];
            sorted.sort(function(a, b) {
                let valA, valB;
                
                switch (interfaceSortField) {
                    case 'status':
                        valA = a.disabled ? 0 : (a.running ? 2 : 1);
                        valB = b.disabled ? 0 : (b.running ? 2 : 1);
                        break;
                    case 'name':
                        valA = (a.name || '').toLowerCase();
                        valB = (b.name || '').toLowerCase();
                        break;
                    case 'type':
                        valA = (a.type || '').toLowerCase();
                        valB = (b.type || '').toLowerCase();
                        break;
                    case 'mac':
                        valA = (a.mac_address || '').toLowerCase();
                        valB = (b.mac_address || '').toLowerCase();
                        break;
                    case 'tx':
                        valA = a.tx_byte || 0;
                        valB = b.tx_byte || 0;
                        break;
                    case 'rx':
                        valA = a.rx_byte || 0;
                        valB = b.rx_byte || 0;
                        break;
                    case 'mtu':
                        valA = parseInt(a.mtu) || 0;
                        valB = parseInt(b.mtu) || 0;
                        break;
                    default:
                        return 0;
                }
                
                if (valA < valB) return interfaceSortDirection === 'asc' ? -1 : 1;
                if (valA > valB) return interfaceSortDirection === 'asc' ? 1 : -1;
                return 0;
            });
            
            return sorted;
        }

        function getSortIcon(field) {
            if (interfaceSortField !== field) {
                return '';
            }
            return interfaceSortDirection === 'asc' 
                ? '<small class="interface-sort-hint">升序</small>' 
                : '<small class="interface-sort-hint">降序</small>';
        }

        function renderInterfaces(interfaces) {
            const mergedInterfaces = mergeInterfacesWithCache(interfaces || []);
            currentInterfaces = mergedInterfaces;
            
            const sortedInterfaces = getSortedInterfaces(currentInterfaces);

            let tableHtml = `
                <div class="config-card">
                    <div class="config-card-header">
                        <h3>接口列表</h3>
                        <div class="interface-buttons">
                            <button class="btn btn-enable" id="btn-enable-interface" onclick="handleEnableInterface()" disabled style="opacity: 0.5;">启用</button>
                            <button class="btn btn-disable" id="btn-disable-interface" onclick="handleDisableInterface()" disabled style="opacity: 0.5;">禁用</button>
                            <button class="btn btn-refresh" onclick="refreshInterfaces()">刷新</button>
                        </div>
                    </div>
                    <div class="config-card-body">
                        <table class="data-table">
                            <thead>
                                <tr>
                                    <th class="sortable" onclick="sortInterfaces('status')">状态 ${getSortIcon('status')}</th>
                                    <th class="sortable" onclick="sortInterfaces('name')">接口名称 ${getSortIcon('name')}</th>
                                    <th class="sortable" onclick="sortInterfaces('type')">接口类型 ${getSortIcon('type')}</th>
                                    <th class="sortable" onclick="sortInterfaces('mac')">MAC地址 ${getSortIcon('mac')}</th>
                                    <th class="sortable" style="text-align: right;" onclick="sortInterfaces('tx')">发送速率 ${getSortIcon('tx')}</th>
                                    <th class="sortable" style="text-align: right;" onclick="sortInterfaces('rx')">接收速率 ${getSortIcon('rx')}</th>
                                    <th class="sortable" style="text-align: right;" onclick="sortInterfaces('mtu')">MTU ${getSortIcon('mtu')}</th>
                                </tr>
                            </thead>
                            <tbody id="interface-table-body">
            `;

            if (sortedInterfaces && sortedInterfaces.length > 0) {
                sortedInterfaces.forEach(function(iface) {
                    const statusClass = iface.disabled ? 'status-disabled' : (iface.running ? 'status-online' : 'status-offline');
                    const statusText = iface.disabled ? '禁用' : (iface.running ? '运行' : '停止');
                    const safeName = iface.name.replace(/[^a-zA-Z0-9_-]/g, '_');
                    const rowClass = iface.disabled ? 'interface-disabled' : '';
                    const macAddress = iface.mac_address || '--';
                    
                    let txText = '0 Kbps';
                    let rxText = '0 Kbps';
                    if (iface.disabled) {
                        txText = '--';
                        rxText = '--';
                    } else if (lastTrafficData[iface.name]) {
                        txText = formatBitrate(lastTrafficData[iface.name].tx_bps || 0);
                        rxText = formatBitrate(lastTrafficData[iface.name].rx_bps || 0);
                    }

                    tableHtml += `
                        <tr data-interface="${iface.name}" class="${rowClass}" onclick="selectInterface('${iface.name}', ${iface.disabled})" ondblclick="showInterfaceDetail('${iface.name}')">
                            <td><span class="status-badge ${statusClass}">${statusText}</span></td>
                            <td style="font-weight: 600; color: #2c3e50;">${iface.name}</td>
                            <td>${iface.type}</td>
                            <td style="font-family: monospace;">${macAddress}</td>
                            <td style="color: #27ae60; text-align: right;" id="tx-${safeName}">${txText}</td>
                            <td style="color: #3498db; text-align: right;" id="rx-${safeName}">${rxText}</td>
                            <td style="text-align: right;">${iface.mtu}</td>
                        </tr>
                    `;
                });
            } else {
                tableHtml += `
                    <tr>
                        <td colspan="7" style="text-align: center; color: #999; padding: 40px;">
                            暂无接口数据
                        </td>
                    </tr>
                `;
            }

            tableHtml += `
                            </tbody>
                        </table>
                    </div>
                </div>
            `;

            document.getElementById('content-body').innerHTML = tableHtml;
            
            if (selectedInterface) {
                const row = document.querySelector(`tr[data-interface="${selectedInterface.name}"]`);
                if (row) {
                    row.classList.add('selected-row');
                }
                updateInterfaceButtons();
            }
        }

        let detailUpdateInterval = null;
        let currentDetailInterface = null;

        function showInterfaceDetail(interfaceName) {
            const modal = document.getElementById('interface-detail-modal');
            if (!modal) return;

            currentDetailInterface = interfaceName;
            
            const iface = currentInterfaces.find(i => i.name === interfaceName);
            if (iface) {
                updateInterfaceDetailContent(iface);
            }

            modal.classList.add('active');

            if (detailUpdateInterval) {
                clearInterval(detailUpdateInterval);
            }
            
            detailUpdateInterval = setInterval(function() {
                if (currentDetailInterface && currentInterfaces.length > 0) {
                    const currentIface = currentInterfaces.find(i => i.name === currentDetailInterface);
                    if (currentIface) {
                        updateInterfaceDetailContent(currentIface);
                    }
                }
            }, 2000);
        }

        function updateInterfaceDetailContent(iface) {
            const nameEl = document.getElementById('detail-name');
            const statusEl = document.getElementById('detail-status');
            const typeEl = document.getElementById('detail-type');
            const macEl = document.getElementById('detail-mac');
            const mtuEl = document.getElementById('detail-mtu');
            const txEl = document.getElementById('detail-tx');
            const rxEl = document.getElementById('detail-rx');
            const txTotalEl = document.getElementById('detail-tx-total');
            const rxTotalEl = document.getElementById('detail-rx-total');
            const iconEl = document.getElementById('detail-icon');
            const linkDownTimeEl = document.getElementById('detail-link-down-time');
            const linkUpTimeEl = document.getElementById('detail-link-up-time');
            const linkDownsEl = document.getElementById('detail-link-downs');
            const slaveEl = document.getElementById('detail-slave');

            if (nameEl) nameEl.textContent = iface.name;
            
            if (statusEl) {
                let statusText, statusClass;
                if (iface.disabled) {
                    statusText = '禁用';
                    statusClass = 'status-disabled';
                } else if (iface.running) {
                    statusText = '运行';
                    statusClass = 'status-online';
                } else {
                    statusText = '停止';
                    statusClass = 'status-offline';
                }
                statusEl.innerHTML = `<span class="status-badge ${statusClass}">${statusText}</span>`;
            }
            
            if (typeEl) typeEl.textContent = iface.type || '--';
            if (macEl) macEl.textContent = iface.mac_address || '--';
            if (mtuEl) mtuEl.textContent = iface.mtu || '--';
            
            const safeName = iface.name.replace(/[^a-zA-Z0-9_-]/g, '_');
            const txRateEl = document.getElementById(`tx-${safeName}`);
            const rxRateEl = document.getElementById(`rx-${safeName}`);
            
            if (txEl) txEl.textContent = txRateEl ? txRateEl.textContent : (iface.disabled ? '--' : '0 Kbps');
            if (rxEl) rxEl.textContent = rxRateEl ? rxRateEl.textContent : (iface.disabled ? '--' : '0 Kbps');
            
            if (txTotalEl) txTotalEl.textContent = (iface.tx_byte !== undefined && iface.tx_byte !== null) ? formatBytes(iface.tx_byte) : '--';
            if (rxTotalEl) rxTotalEl.textContent = (iface.rx_byte !== undefined && iface.rx_byte !== null) ? formatBytes(iface.rx_byte) : '--';
            
            if (linkDownTimeEl) linkDownTimeEl.textContent = iface.last_link_down_time || '--';
            if (linkUpTimeEl) linkUpTimeEl.textContent = iface.last_link_up_time || '--';
            if (linkDownsEl) linkDownsEl.textContent = iface.link_downs !== undefined ? String(iface.link_downs) : '0';
            if (slaveEl) {
                if (iface.slave) {
                    slaveEl.innerHTML = '<span class="status-badge status-online">是</span>';
                } else {
                    slaveEl.innerHTML = '<span class="status-badge status-offline">否</span>';
                }
            }
            
            if (iconEl) {
                if (iface.disabled) {
                    iconEl.textContent = '⏸️';
                } else if (iface.running) {
                    iconEl.textContent = '📡';
                } else {
                    iconEl.textContent = '📴';
                }
            }
        }

        function hideInterfaceDetail() {
            const modal = document.getElementById('interface-detail-modal');
            if (modal) {
                modal.classList.remove('active');
            }
            
            if (detailUpdateInterval) {
                clearInterval(detailUpdateInterval);
                detailUpdateInterval = null;
            }
            
            currentDetailInterface = null;
        }

        function formatBytes(bytes) {
            if (bytes === undefined || bytes === null) return '--';
            if (bytes >= 1024 * 1024 * 1024) {
                return (bytes / (1024 * 1024 * 1024)).toFixed(2) + ' GB';
            } else if (bytes >= 1024 * 1024) {
                return (bytes / (1024 * 1024)).toFixed(2) + ' MB';
            } else if (bytes >= 1024) {
                return (bytes / 1024).toFixed(2) + ' KB';
            } else {
                return bytes + ' B';
            }
        }

        function showLoginErrorModal(message) {
            const modal = document.getElementById('login-error-modal');
            const messageEl = document.getElementById('login-error-message');
            const confirmBtn = document.getElementById('login-error-confirm');
            
            if (!modal) return;
            
            if (messageEl) {
                messageEl.textContent = message;
            }
            
            modal.classList.add('active');
            
            if (confirmBtn) {
                confirmBtn.onclick = function() {
                    modal.classList.remove('active');
                };
            }
        }

        let interfaceModalCallback = null;

        function showInterfaceModal(itemName, isDisabled, callback) {
            const modal = document.getElementById('interface-modal');
            const icon = document.getElementById('interface-modal-icon');
            const title = document.getElementById('interface-modal-title');
            const message = document.getElementById('interface-modal-message');
            const confirmBtn = document.getElementById('interface-modal-confirm');
            const cancelBtn = document.getElementById('interface-modal-cancel');

            if (!modal) return;

            const action = isDisabled ? '启用' : '禁用';
            icon.textContent = isDisabled ? '✅' : '⚠️';
            title.textContent = `${action}${itemName}`;
            title.style.color = isDisabled ? '#27ae60' : '#e74c3c';
            message.textContent = `确定要${action}${itemName}吗？`;
            
            confirmBtn.textContent = action;
            confirmBtn.className = 'interface-confirm-btn' + (isDisabled ? ' enable' : '');

            interfaceModalCallback = callback;

            modal.classList.add('active');
        }

        function showConfirmModal(icon, title, titleColor, messageText, confirmText, confirmClass, callback) {
            const modal = document.getElementById('interface-modal');
            const iconEl = document.getElementById('interface-modal-icon');
            const titleEl = document.getElementById('interface-modal-title');
            const messageEl = document.getElementById('interface-modal-message');
            const confirmBtn = document.getElementById('interface-modal-confirm');

            if (!modal) return;

            iconEl.textContent = icon;
            titleEl.textContent = title;
            titleEl.style.color = titleColor;
            messageEl.textContent = messageText;
            confirmBtn.textContent = confirmText;
            confirmBtn.className = 'interface-confirm-btn' + (confirmClass ? ' ' + confirmClass : '');

            interfaceModalCallback = callback;

            modal.classList.add('active');
        }

        function hideInterfaceModal() {
            const modal = document.getElementById('interface-modal');
            if (modal) {
                modal.classList.remove('active');
            }
            interfaceModalCallback = null;
        }

        document.addEventListener('DOMContentLoaded', function() {
            const interfaceModalCancel = document.getElementById('interface-modal-cancel');
            const interfaceModalConfirm = document.getElementById('interface-modal-confirm');

            if (interfaceModalCancel) {
                interfaceModalCancel.addEventListener('click', function() {
                    hideInterfaceModal();
                });
            }

            if (interfaceModalConfirm) {
                interfaceModalConfirm.addEventListener('click', function() {
                    if (interfaceModalCallback) {
                        interfaceModalCallback();
                    }
                    hideInterfaceModal();
                });
            }
        });

        function toggleInterface(interfaceName, isDisabled) {
            showInterfaceModal(interfaceName, isDisabled, function() {
                fetch(`${API_BASE}/api/interface-toggle?ip=${currentSession.ip}&interface=${encodeURIComponent(interfaceName)}&action=${isDisabled ? 'enable' : 'disable'}`)
                    .then(response => response.json())
                    .then(result => {
                        if (result.status === 'success') {
                            refreshInterfaces();
                            // 同时刷新无线接口列表（如果 WebSocket 处于活跃状态）
                            refreshWirelessInterfacesIfActive();
                        } else {
                            showNetworkAlert('操作失败: ' + result.message, 'error');
                        }
                    })
                    .catch(error => {
                        showNetworkAlert('操作失败: ' + error.message, 'error');
                    });
            });
        }

        function refreshWirelessInterfacesIfActive() {
            if (wirelessInterfacesWs) {
                disconnectWirelessInterfacesWebSocket();
                setTimeout(function() {
                    connectWirelessInterfacesWebSocket();
                }, 100);
            }
        }

        async function refreshInterfaces() {
            disconnectInterfaceWebSocket();
            await new Promise(resolve => setTimeout(resolve, 100));
            connectInterfaceWebSocket();
        }
        
        let ws = null;
        let isReconnecting = false;
        let isLoggingOut = false;
        let reconnectTimer = null;
        let reconnectCountdown = 60;
        let reconnectProgress = null;
        let reconnectInterval = null;
        let lastWsMessageTime = 0;
        let wsHeartbeatTimer = null;
        const WS_HEARTBEAT_TIMEOUT = 45000;
        let offlineDebounceTimer = null;
        const OFFLINE_DEBOUNCE_DELAY = 3000;

        function showReconnectModal() {
            const modal = document.getElementById('reconnect-modal');
            const timer = document.getElementById('reconnect-timer');
            const progressBar = document.getElementById('reconnect-progress-bar');
            const title = document.querySelector('.reconnect-title');
            const message = document.querySelector('.reconnect-message');
            if (modal) {
                modal.classList.add('active');
            }
            reconnectCountdown = 60;
            reconnectProgress = 100;
            if (title) {
                title.textContent = '设备离线';
            }
            if (message) {
                message.textContent = '正在尝试重新连接...';
            }
            if (timer) {
                timer.textContent = '剩余时间：' + reconnectCountdown + ' 秒';
                timer.style.display = 'block';
            }
            if (progressBar) {
                progressBar.style.width = '100%';
            }

            reconnectTimer = setInterval(() => {
                reconnectCountdown--;
                reconnectProgress = (reconnectCountdown / 60) * 100;
                if (timer) {
                    timer.textContent = '剩余时间：' + reconnectCountdown + ' 秒';
                }
                if (progressBar) {
                    progressBar.style.width = reconnectProgress + '%';
                }

                if (reconnectCountdown <= 0) {
                    stopArpPolling();
                    clearReconnectTimer();
                    hideReconnectModal();
                    hideConfigInterface();
                    document.getElementById('login-section').style.display = 'block';
                    document.getElementById('config-section').style.display = 'none';
                }
            }, 1000);

            reconnectInterval = setInterval(() => {
                reconnectWebSocket();
            }, 2000);
        }

        function hideReconnectModal() {
            const modal = document.getElementById('reconnect-modal');
            if (modal) {
                modal.classList.remove('active');
            }
        }

        function clearReconnectTimer() {
            if (reconnectTimer) {
                clearInterval(reconnectTimer);
                reconnectTimer = null;
            }
            if (reconnectInterval) {
                clearInterval(reconnectInterval);
                reconnectInterval = null;
            }
        }

        function startWsHeartbeatCheck() {
            stopWsHeartbeatCheck();
            wsHeartbeatTimer = setInterval(() => {
                if (!ws || ws.readyState !== WebSocket.OPEN) {
                    return;
                }
                const elapsed = Date.now() - lastWsMessageTime;
                if (elapsed > WS_HEARTBEAT_TIMEOUT) {
                    console.warn(`[心跳检测] ${elapsed}ms 未收到消息，判定连接断开`);
                    stopWsHeartbeatCheck();
                    // 主动关闭已失效的 WebSocket 连接
                    try { ws.close(); } catch(e) {}
                    ws = null;
                    if (!isLoggingOut && !reconnectTimer) {
                        showReconnectModal();
                        reconnectWebSocket();
                    }
                } else if (elapsed > 20000) {
                    // 20秒没收到消息，可能是后端心跳延迟，打印警告但不重连
                    console.warn(`[心跳检测] ${elapsed}ms 未收到消息，等待中...`);
                }
            }, 10000);  // 每10秒检查一次，降低检测频率
        }

        function stopWsHeartbeatCheck() {
            if (wsHeartbeatTimer) {
                clearInterval(wsHeartbeatTimer);
                wsHeartbeatTimer = null;
            }
        }

        function reconnectFailed() {
            clearReconnectTimer();
            stopWsHeartbeatCheck();
            hideReconnectModal();
            alert('设备连接重连失败，已离线');
            hideConfigInterface();
            document.getElementById('login-section').style.display = 'block';
            document.getElementById('config-section').style.display = 'none';
            document.getElementById('error-message').textContent = '设备连接已断开';
            document.getElementById('error-message').style.display = 'block';
        }

        function reconnectSuccess() {
            clearReconnectTimer();
            stopArpPolling();
            clearDeviceTimeCache();
            const title = document.querySelector('.reconnect-title');
            const message = document.querySelector('.reconnect-message');
            const timer = document.getElementById('reconnect-timer');
            const progressBar = document.getElementById('reconnect-progress-bar');
            if (title) title.textContent = '重连成功';
            if (message) message.textContent = '设备已恢复连接';
            if (timer) timer.style.display = 'none';
            if (progressBar) progressBar.style.width = '0%';
            setTimeout(() => {
                hideReconnectModal();
                if (title) title.textContent = '设备离线';
                if (message) message.textContent = '正在尝试重新连接...';
                if (timer) timer.style.display = 'block';
                if (progressBar) progressBar.style.width = '100%';
            }, 1500);
        }

        let arpPollingTimer = null;
        let isArpChecking = false;

        function startArpPolling() {
            if (isArpChecking) {
                console.log('[ARP] 正在检查中，跳过启动轮询');
                return;
            }
            console.log('[ARP] startArpPolling called');
            stopArpPolling();
            arpPollingTimer = setInterval(() => {
                if (isArpChecking) {
                    console.log('[ARP] 正在检查中，跳过本次轮询');
                    return;
                }
                console.log('[ARP] arpPolling interval tick, isLoggingOut=' + isLoggingOut + ', reconnectTimer=' + (!!reconnectTimer));
                if (isLoggingOut || !reconnectTimer) {
                    stopArpPolling();
                    return;
                }
                checkArpOnce(currentSession.ip);
            }, 5000);
            console.log('[ARP] arpPollingTimer started');
        }

        function stopArpPolling() {
            if (arpPollingTimer) {
                clearInterval(arpPollingTimer);
                arpPollingTimer = null;
            }
        }

        async function checkArpOnce(ip) {
            console.log('[ARP] checkArpOnce sending ARP request to ' + ip);
            isArpChecking = true;
            try {
                const response = await fetch(`${API_BASE}/api/check-arp`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ ip: ip })
                });
                const data = await response.json();
                console.log('[ARP] checkArpOnce received: ' + JSON.stringify(data));

                if (data.reachable) {
                    console.log('ARP检测设备可达，开始登录');
                    stopArpPolling();
                    doLogin();
                }
            } catch (e) {
                console.log('ARP检测失败: ' + e);
            } finally {
                isArpChecking = false;
            }
        }

        async function doLogin() {
            if (isLoggingOut || !reconnectTimer) return;

            try {
                const response = await fetch(`${API_BASE}/api/connect`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        ip: currentSession.ip,
                        username: currentSession.username,
                        password: currentSession.password
                    })
                });
                const result = await response.json();

                if (result.status === 'success') {
                    console.log('设备重新登录成功');
                    reconnectSuccess();
                    currentSession.api_version = result.api_version;
                    currentSession.routeros_version = result.routeros_version;
                    currentSession.board_name = result.board_name;
                    currentSession.identity = result.identity || result.ip;
                    connectWebSocket();
                } else {
                    console.log('设备重新登录失败: ' + result.message);
                    startArpPolling();
                }
            } catch (error) {
                console.error('重新登录失败:', error);
                startArpPolling();
            }
        }

        function reconnectWebSocket() {
            console.log('[ARP] reconnectWebSocket called, isLoggingOut=' + isLoggingOut + ', reconnectTimer=' + (!!reconnectTimer) + ', isArpChecking=' + isArpChecking);
            if (isLoggingOut) return;
            if (!reconnectTimer) return;
            if (isArpChecking) {
                console.log('[ARP] 正在检查中，跳过reconnectWebSocket');
                return;
            }

            console.log('开始ARP轮询，等待设备上线...');
            startArpPolling();
            checkArpOnce(currentSession.ip);
        }

        function connectWebSocket() {
            if (!currentSession) return;

            if (ws) {
                isReconnecting = true;
                ws.close();
                ws = null;
            }

            const wsUrl = `${WS_BASE}`;
            ws = new WebSocket(wsUrl);
            
            ws.onopen = function() {
                console.log('WebSocket 已连接');
                isReconnecting = false;
                lastWsMessageTime = Date.now();
                startWsHeartbeatCheck();

                ws.send(JSON.stringify({
                    ip: currentSession.ip,
                    mac: currentSession.mac || '',
                    username: currentSession.username,
                    password: currentSession.password || ''
                }));
            };
            
            ws.onmessage = function(event) {
                lastWsMessageTime = Date.now();
                console.log('WebSocket收到消息:', event.data.substring ? event.data.substring(0, 200) : event.data);
                const data = JSON.parse(event.data);
                
                if (data.error) {
                    console.error('错误:', data.error);
                    return;
                }
                
                if (data.status === 'connected') {
                    console.log('设备已连接');
                    if (offlineDebounceTimer) {
                        console.log('连接已恢复，清除离线防抖定时器');
                        clearTimeout(offlineDebounceTimer);
                        offlineDebounceTimer = null;
                    }
                    if (reconnectTimer) {
                        reconnectSuccess();
                    }
                    return;
                }
                
                if (data.status === 'device_offline') {
                    console.log('收到设备离线通知');
                    if (offlineDebounceTimer) {
                        console.log('离线防抖中，忽略重复通知');
                        return;
                    }
                    offlineDebounceTimer = setTimeout(() => {
                        offlineDebounceTimer = null;
                        if (!isLoggingOut && !reconnectTimer) {
                            console.log('离线防抖确认，触发重连');
                            showReconnectModal();
                            reconnectWebSocket();
                        }
                    }, OFFLINE_DEBOUNCE_DELAY);
                    return;
                }

                if (data.action === 'ping') {
                    ws.send(JSON.stringify({action: 'pong'}));
                    return;
                }
                
            };
            
            ws.onclose = function() {
                console.log('WebSocket 已断开, isReconnecting=' + isReconnecting);
                stopWsHeartbeatCheck();
                if (isReconnecting) {
                    console.log('正在重连，忽略 onclose');
                    return;
                }
                // 清除设备时间缓存
                clearDeviceTimeCache();
                if (!isLoggingOut && !reconnectTimer) {
                    showReconnectModal();
                    reconnectWebSocket();
                } else {
                    console.log('用户退出登录，不重连WebSocket');
                }
            };
            
            ws.onerror = function(error) {
                console.error('WebSocket 错误:', error);
                if (!isLoggingOut) {
                    if (!reconnectTimer) {
                        showReconnectModal();
                        reconnectWebSocket();
                    }
                }
            };
        }
        
        window.onload = function() {
            const rememberPassword = document.getElementById('remember-password');
            const username = document.getElementById('username');
            const password = document.getElementById('password');
            
            if (localStorage.getItem('remember') === 'true') {
                rememberPassword.checked = true;
                username.value = localStorage.getItem('username') || '';
                password.value = localStorage.getItem('password') || '';
            }
            
            // 页面加载时触发一次设备发现
            fetch(`${API_BASE}/api/discover`, { method: 'POST' }).catch(() => {});
            fetchDevices();
            startAutoRefresh();
        };
        
        window.onbeforeunload = function() {
            stopAutoRefresh();
        };
        
        document.getElementById('remember-password').addEventListener('change', function() {
            const username = document.getElementById('username');
            const password = document.getElementById('password');
            
            if (this.checked) {
                localStorage.setItem('remember', 'true');
                localStorage.setItem('username', username.value);
                localStorage.setItem('password', password.value);
            } else {
                localStorage.removeItem('remember');
                localStorage.removeItem('username');
                localStorage.removeItem('password');
            }
        });
        
        document.getElementById('username').addEventListener('input', function() {
            if (document.getElementById('remember-password').checked) {
                localStorage.setItem('username', this.value);
            }
        });
        
        document.getElementById('password').addEventListener('input', function() {
            if (document.getElementById('remember-password').checked) {
                localStorage.setItem('password', this.value);
            }
        });
        
        const effectArea = document.getElementById('effect-area');
        const loginPanel = document.querySelector('.login-panel');
        const particles = [];
        const maxParticles = 50;
        const colors = ['#fff', '#f0f0f0', '#e0e0e0', '#d0d0d0', '#c0c0c0'];
        
        function createParticle(x, y) {
            if (particles.length >= maxParticles) {
                const oldParticle = particles.shift();
                if (oldParticle.element && oldParticle.element.parentNode) {
                    oldParticle.element.parentNode.removeChild(oldParticle.element);
                }
            }
            
            const particle = document.createElement('div');
            particle.className = 'particle';
            
            const size = Math.random() * 6 + 3;
            const color = colors[Math.floor(Math.random() * colors.length)];
            
            particle.style.width = size + 'px';
            particle.style.height = size + 'px';
            particle.style.backgroundColor = color;
            particle.style.left = x + 'px';
            particle.style.top = y + 'px';
            particle.style.opacity = '0.6';
            
            effectArea.appendChild(particle);
            
            particles.push({
                element: particle,
                x, y,
                vx: (Math.random() - 0.5) * 2,
                vy: (Math.random() - 0.5) * 2,
                life: 1,
                decay: Math.random() * 0.02 + 0.01
            });
        }
        
        function updateParticles() {
            for (let i = particles.length - 1; i >= 0; i--) {
                const p = particles[i];
                p.x += p.vx;
                p.y += p.vy;
                p.life -= p.decay;
                
                if (p.life <= 0) {
                    if (p.element && p.element.parentNode) {
                        p.element.parentNode.removeChild(p.element);
                    }
                    particles.splice(i, 1);
                } else {
                    p.element.style.left = p.x + 'px';
                    p.element.style.top = p.y + 'px';
                    p.element.style.opacity = p.life * 0.6;
                    p.element.style.transform = `scale(${p.life})`;
                }
            }
            requestAnimationFrame(updateParticles);
        }
        
        loginPanel.addEventListener('mousemove', function(e) {
            const rect = loginPanel.getBoundingClientRect();
            for (let i = 0; i < 2; i++) {
                createParticle(e.clientX - rect.left + (Math.random() - 0.5) * 10, e.clientY - rect.top + (Math.random() - 0.5) * 10);
            }
        });

        const reconnectCancelBtn = document.getElementById('reconnect-cancel-btn');
        if (reconnectCancelBtn) {
            reconnectCancelBtn.addEventListener('click', function() {
                clearReconnectTimer();
                hideReconnectModal();
                hideConfigInterface();
                document.getElementById('login-section').style.display = 'block';
                document.getElementById('config-section').style.display = 'none';
            });
        }

        updateParticles();