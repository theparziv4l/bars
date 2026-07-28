import { useEffect, useRef, useCallback } from 'react'
import { useAppStore, useExecutionsStore, useChecklistsStore, useProjectsStore, useHostsStore, useAuthStore, useFindingsStore } from '../stores'

export function useWebSocket(channel = 'default') {
    const wsRef = useRef(null)
    const reconnectTimeoutRef = useRef(null)
    const hasConnectedRef = useRef(false)
    const { setWsConnected, addNotification } = useAppStore()
    const { updateExecution, removeFromRunning } = useExecutionsStore()
    const { updateItemStatus, updateItemClaim, updateBatchProgress, clearBatchProgress } = useChecklistsStore()

    const handleMessage = useCallback((message) => {
        switch (message.type) {
            case 'output':
                // Real-time output streaming
                // Could update a terminal component
                break

            case 'execution_status': {
                updateExecution({ ...message.data, id: message.data.execution_id })

                const details = message.data.details || {}
                const itemId = message.data.item_id
                const isBatchParent = !!details.is_batch_parent
                const activeBatch = itemId ? useChecklistsStore.getState().batchProgress[itemId] : null
                const activeBatchRunning = activeBatch && activeBatch.status === 'running'

                if (isBatchParent && itemId) {
                    const isTerminal = ['completed', 'failed', 'cancelled', 'timeout'].includes(message.data.status)
                    updateBatchProgress(itemId, {
                        completed: details.completed_count ?? 0,
                        success: details.success_count ?? 0,
                        total: details.total ?? 0,
                        status: message.data.status,
                        executionId: message.data.execution_id,
                    })
                    updateItemStatus(itemId, message.data.status, message.data.execution_id)
                    if (isTerminal) {
                        setTimeout(() => clearBatchProgress(itemId), 30000)
                    }
                } else if (!activeBatchRunning) {
                    updateItemStatus(itemId, message.data.status, message.data.execution_id)
                }

                if (['completed', 'failed', 'cancelled', 'timeout'].includes(message.data.status)) {
                    removeFromRunning(message.data.execution_id)
                    // Server-computed summaries (project progress, host tags/filters) move
                    // when an execution finishes; pages holding them locally listen here.
                    window.dispatchEvent(new CustomEvent('execution_finished', { detail: message.data }))
                }
                break
            }

            case 'host_created':
            case 'host_updated': {
                // Only refresh if the event is for the project currently on screen —
                // events broadcast to all clients, and blindly refetching another
                // project's hosts would swap the visible asset list to the wrong scope.
                // host_updated matters as much as host_created: it carries the tags and
                // domain that {targets} resolution and the tag filters are built from.
                const hostsPid = useHostsStore.getState().currentProjectId
                if (hostsPid != null && String(message.data.project_id) === String(hostsPid)) {
                    useHostsStore.getState().refetchHosts()
                }
                // Pages holding a single host in local state (HostDashboard) listen for this.
                window.dispatchEvent(new CustomEvent('host_changed', {
                    detail: { ...(message.data?.data || {}), project_id: message.data.project_id },
                }))
                break
            }

            case 'item_updated': {
                // An execution was synced into this project's checklist item.
                const itemsPid = useChecklistsStore.getState().currentProjectId
                if (itemsPid != null && String(message.data.project_id) === String(itemsPid)) {
                    useChecklistsStore.getState().fetchGroups(itemsPid).catch(() => {})
                }
                break
            }

            case 'finding_created': {
                const findingsPid = useFindingsStore.getState().findingsProjectId
                if (findingsPid != null && String(message.data.project_id) === String(findingsPid)) {
                    useFindingsStore.getState().refetchFindings().catch(() => {})
                }
                break
            }

            case 'variable_updated': {
                const varsPid = useProjectsStore.getState().variablesProjectId
                if (varsPid != null && String(message.data.project_id) === String(varsPid)) {
                    useProjectsStore.getState().fetchProjectVariables(varsPid)
                }
                break
            }

            case 'alert':
                addNotification({
                    type: message.data.severity,
                    title: 'Alert Triggered',
                    message: message.data.matched_text,
                    executionId: message.data.execution_id,
                })
                break

            case 'item_claimed':
            case 'item_taken_over':
                updateItemClaim(message.data.item_id, {
                    claim_id: message.data.claim_id,
                    claimed_by_user_id: message.data.claimed_by_user_id,
                    claimed_by_username: message.data.claimed_by_username,
                    host_id: message.data.host_id ?? null,
                    lease_expires_at: message.data.lease_expires_at,
                    is_active: true,
                })
                break

            case 'item_released':
                updateItemClaim(message.data.item_id, {
                    claim_id: null,
                    claimed_by_user_id: null,
                    claimed_by_username: null,
                    host_id: message.data.host_id ?? null,
                    lease_expires_at: null,
                    is_active: false,
                })
                break

            case 'shares_discovered':
                window.dispatchEvent(new CustomEvent('shares_discovered', { detail: message.data?.data || message.data }))
                break

            case 'flow_completed':
            case 'flow_failed':
                addNotification({
                    type: message.type === 'flow_completed' ? 'success' : 'error',
                    title: message.type === 'flow_completed' ? 'Flow Completed' : 'Flow Failed',
                    message: message.data.error || `Flow execution ${message.data.flow_execution_id}`,
                })
                // FlowViewer keeps flow status in local state; let it refresh itself.
                window.dispatchEvent(new CustomEvent('flow_finished', { detail: message.data }))
                break

            case 'heartbeat':
            case 'pong':
                // Keep-alive, no action needed
                break

            default:
                console.log('Unknown message type:', message.type)
        }
    }, [updateExecution, removeFromRunning, updateItemStatus, updateItemClaim, updateBatchProgress, clearBatchProgress, addNotification])

    const handleMessageRef = useRef(handleMessage)
    handleMessageRef.current = handleMessage

    const connect = useCallback(() => {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
        const token = useAuthStore.getState().token
        const wsUrl = token
            ? `${protocol}//${window.location.host}/ws?token=${encodeURIComponent(token)}`
            : `${protocol}//${window.location.host}/ws`

        wsRef.current = new WebSocket(wsUrl)

        wsRef.current.onopen = () => {
            console.log('WebSocket connected')
            setWsConnected(true)

            // Subscribe to channel if not default
            if (channel !== 'default') {
                wsRef.current.send(JSON.stringify({ type: 'subscribe', channel }))
            }

            // On RE-connect, reconcile state missed during the gap: a terminal
            // execution_status emitted while disconnected is lost, which otherwise
            // leaves checklist items stuck showing "running".
            if (hasConnectedRef.current) {
                const pid = useChecklistsStore.getState().currentProjectId
                if (pid != null) {
                    useChecklistsStore.getState().fetchGroups(pid).catch(() => {})
                }
            }
            hasConnectedRef.current = true
        }

        wsRef.current.onmessage = (event) => {
            try {
                const message = JSON.parse(event.data)
                handleMessageRef.current(message)
            } catch (e) {
                console.error('Failed to parse WebSocket message:', e)
            }
        }

        wsRef.current.onclose = () => {
            console.log('WebSocket disconnected')
            setWsConnected(false)

            // Attempt reconnect after 3 seconds
            reconnectTimeoutRef.current = setTimeout(connect, 3000)
        }

        wsRef.current.onerror = (error) => {
            console.error('WebSocket error:', error)
        }
    }, [channel, setWsConnected])

    useEffect(() => {
        connect()

        return () => {
            if (reconnectTimeoutRef.current) {
                clearTimeout(reconnectTimeoutRef.current)
            }
            if (wsRef.current) {
                // Detach onclose BEFORE closing so the async close event can't schedule a
                // reconnect after unmount (zombie socket lineage that keeps mutating stores).
                wsRef.current.onclose = null
                wsRef.current.close()
            }
        }
    }, [connect])

    const send = useCallback((data) => {
        if (wsRef.current?.readyState === WebSocket.OPEN) {
            wsRef.current.send(JSON.stringify(data))
        }
    }, [])

    return { send }
}

export function useExecutionWebSocket(executionId) {
    return useWebSocket(`execution:${executionId}`)
}
