import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import ReactFlow, {
    Background,
    Controls,
    MarkerType,
    useNodesState,
    useEdgesState
} from 'reactflow';
import 'reactflow/dist/style.css';
import {
    Play, Square, Pause, RefreshCw, ArrowLeft, CheckCircle2, XCircle,
    Clock, Loader2, X, Terminal, ChevronDown, History, Target
} from 'lucide-react';
import toast from 'react-hot-toast';
import { flowsApi, hostsApi, executionsApi } from '../services/api';
import OutputViewer from '../components/OutputViewer';

const statusColors = {
    pending: { bg: '#374151', border: '#4B5563', text: '#9CA3AF' },
    running: { bg: '#1E3A8A', border: '#3B82F6', text: '#93C5FD' },
    completed: { bg: '#064E3B', border: '#10B981', text: '#6EE7B7' },
    failed: { bg: '#7F1D1D', border: '#EF4444', text: '#FCA5A5' },
    cancelled: { bg: '#78350F', border: '#F59E0B', text: '#FDE68A' },
    timeout: { bg: '#7F1D1D', border: '#EF4444', text: '#FCA5A5' },
    paused: { bg: '#4C1D95', border: '#8B5CF6', text: '#C4B5FD' },
};

const StatusIcon = ({ status }) => {
    switch (status) {
        case 'completed': return <CheckCircle2 size={14} className="text-green-400" />;
        case 'running': return <Loader2 size={14} className="text-blue-400 animate-spin" />;
        case 'failed': return <XCircle size={14} className="text-red-400" />;
        case 'cancelled': return <XCircle size={14} className="text-yellow-400" />;
        case 'paused': return <Pause size={14} className="text-purple-400" />;
        default: return <Clock size={14} className="text-gray-400" />;
    }
};

export default function FlowViewer() {
    const { flowId } = useParams();
    const navigate = useNavigate();

    const [flow, setFlow] = useState(null);
    const [flowStatus, setFlowStatus] = useState(null);
    const [nodes, setNodes, onNodesChange] = useNodesState([]);
    const [edges, setEdges, onEdgesChange] = useEdgesState([]);
    const [isExecuting, setIsExecuting] = useState(false);
    const [isPaused, setIsPaused] = useState(false);

    // Execute modal
    const [showExecuteModal, setShowExecuteModal] = useState(false);
    const [hosts, setHosts] = useState([]);
    const [selectedHostIds, setSelectedHostIds] = useState([]);
    const [manualTargets, setManualTargets] = useState('');
    const [flowVars, setFlowVars] = useState({});

    // Execution history
    const [execHistory, setExecHistory] = useState([]);
    const [showHistory, setShowHistory] = useState(false);
    const [selectedExecId, setSelectedExecId] = useState(null);

    // Output panel
    const [outputPanel, setOutputPanel] = useState(null);
    const [outputData, setOutputData] = useState(null);
    const [stepExecHistory, setStepExecHistory] = useState([]);
    const [outputFullscreen, setOutputFullscreen] = useState(false);

    // ---- Data loading ----
    const loadFlow = useCallback(async () => {
        try {
            const response = await flowsApi.get(flowId);
            setFlow(response.data);
        } catch (error) {
            console.error('Failed to load flow:', error);
            toast.error('Failed to load flow');
        }
    }, [flowId]);

    const loadStatus = useCallback(async (execId) => {
        try {
            const response = execId
                ? await flowsApi.getStatusByExec(flowId, execId)
                : await flowsApi.getStatus(flowId);
            setFlowStatus(response.data);
            const status = response.data.overall_status;
            setIsExecuting(status === 'running');
            setIsPaused(status === 'paused');
        } catch (error) {
            console.error('Failed to load status:', error);
        }
    }, [flowId]);

    const loadHistory = useCallback(async () => {
        try {
            const response = await flowsApi.listExecutions(flowId);
            setExecHistory(response.data || []);
        } catch {
            // endpoint may not exist on older backends
        }
    }, [flowId]);

    const loadHosts = useCallback(async () => {
        if (!flow?.project_id) return;
        try {
            const response = await hostsApi.list(flow.project_id, { per_page: 500 });
            // Excluded hosts are out of scope — the backend drops them from flow targets,
            // so never offer them in the picker.
            const items = response.data?.items || response.data || [];
            setHosts(items.filter(h => !h.excluded));
        } catch { /* ignore */ }
    }, [flow?.project_id]);

    // ---- Build graph ----
    useEffect(() => {
        if (!flow) return;
        const newNodes = [];
        const newEdges = [];
        let y = 50;
        const x = 200;
        const orderIndexToNodeIds = {};

        flow.steps.forEach((step, index) => {
            const nodeId = index.toString();
            const orderIdx = step.order_index;
            const stepStatus = flowStatus?.steps?.find(s => s.order_index === step.order_index);
            const status = stepStatus?.status || 'pending';
            const colors = statusColors[status] || statusColors.pending;

            if (!orderIndexToNodeIds[orderIdx]) orderIndexToNodeIds[orderIdx] = [];
            orderIndexToNodeIds[orderIdx].push(nodeId);

            const commandPreview = (step.item_command || '').length > 40
                ? step.item_command.substring(0, 40) + '...'
                : step.item_command || '';

            const targetBadge = step.target_mode && step.target_mode !== 'inherit'
                ? step.target_mode
                : null;

            newNodes.push({
                id: nodeId,
                position: (step.ui_position && step.ui_position.x !== undefined)
                    ? step.ui_position : { x, y },
                data: {
                    stepIndex: index,
                    executionId: stepStatus?.execution_id,
                    itemName: step.item_name,
                    itemId: step.checklist_item_id,
                    hostId: flowStatus?.host_id,
                    label: (
                        <div className="text-left cursor-pointer">
                            <div className="flex items-center gap-2">
                                <StatusIcon status={status} />
                                <span className="font-semibold text-sm">{step.item_name || 'Unknown'}</span>
                            </div>
                            <div className="text-xs opacity-60 mt-1 font-mono">{commandPreview}</div>
                            <div className="flex items-center gap-2 mt-1">
                                {targetBadge && (
                                    <span className="text-[10px] px-1 py-0.5 bg-purple-500/20 text-purple-300 rounded">
                                        {targetBadge}
                                    </span>
                                )}
                                {stepStatus?.started_at && (
                                    <span className="text-[10px] opacity-50">
                                        {new Date(stepStatus.started_at).toLocaleTimeString()}
                                    </span>
                                )}
                                {stepStatus?.is_batch && stepStatus.batch_progress && (
                                    <span className="text-[10px] px-1 py-0.5 bg-blue-500/20 text-blue-300 rounded">
                                        {stepStatus.batch_progress.successful}/{stepStatus.batch_progress.total} targets
                                    </span>
                                )}
                            </div>
                        </div>
                    )
                },
                style: {
                    background: colors.bg,
                    color: colors.text,
                    border: `2px solid ${colors.border}`,
                    borderRadius: '8px',
                    padding: '12px',
                    width: '280px',
                    minHeight: '70px',
                }
            });
            if (!step.ui_position || step.ui_position.x === undefined) y += 140;
        });

        flow.steps.forEach((step, index) => {
            const targetNodeId = index.toString();
            const stepStatus = flowStatus?.steps?.find(s => s.order_index === step.order_index);
            const status = stepStatus?.status || 'pending';

            Object.entries(step.input_mapping || {}).forEach(([, mapping]) => {
                const sourceExpr = typeof mapping === 'string' ? mapping : (mapping.source || '');
                const matches = sourceExpr.match(/\{(\d+)\.(\w+)\}/);
                if (matches && matches[1]) {
                    const sourceOrderIndex = matches[1];
                    const sourceNodeIds = orderIndexToNodeIds[sourceOrderIndex] || [];
                    sourceNodeIds.forEach(sourceNodeId => {
                        const edgeId = `e${sourceNodeId}-${targetNodeId}`;
                        if (!newEdges.find(e => e.id === edgeId)) {
                            newEdges.push({
                                id: edgeId,
                                source: sourceNodeId,
                                target: targetNodeId,
                                animated: status === 'running',
                                markerEnd: { type: MarkerType.ArrowClosed },
                                style: {
                                    stroke: status === 'running' ? '#3B82F6' : '#4B5563',
                                    strokeWidth: 2,
                                }
                            });
                        }
                    });
                }
            });
        });

        // Restore visual edges from flow_definition.edges
        const savedEdges = flow.flow_definition?.edges;
        if (Array.isArray(savedEdges)) {
            savedEdges.forEach(({ source: sourceOrderIdx, target: targetOrderIdx }) => {
                const sourceNodeIds = orderIndexToNodeIds[sourceOrderIdx] || [];
                const targetNodeIds = orderIndexToNodeIds[targetOrderIdx] || [];
                sourceNodeIds.forEach(sourceNodeId => {
                    targetNodeIds.forEach(targetNodeId => {
                        const edgeId = `e${sourceNodeId}-${targetNodeId}`;
                        if (!newEdges.find(e => e.id === edgeId)) {
                            newEdges.push({
                                id: edgeId,
                                source: sourceNodeId,
                                target: targetNodeId,
                                animated: false,
                                markerEnd: { type: MarkerType.ArrowClosed },
                                style: { stroke: '#4B5563', strokeWidth: 2 },
                            });
                        }
                    });
                });
            });
        }

        setNodes(newNodes);
        setEdges(newEdges);
    }, [flow, flowStatus, setNodes, setEdges]);

    const handleNodeClick = useCallback((event, node) => {
        const { executionId, itemName, itemId, hostId } = node.data;
        openOutputPanel(executionId, itemName, itemId, hostId);
    }, []);

    useEffect(() => { loadFlow(); loadStatus(); loadHistory(); }, [loadFlow, loadStatus, loadHistory]);

    // The modal can be opened before `flow` has loaded, in which case loadHosts() bailed
    // out with no retry and the host list stayed empty.
    useEffect(() => {
        if (showExecuteModal && flow?.project_id && hosts.length === 0) loadHosts();
    }, [showExecuteModal, flow?.project_id, hosts.length, loadHosts]);

    // A flow that finishes must leave "running" without a reload.
    useEffect(() => {
        const onFlowFinished = () => { loadStatus(selectedExecId); loadHistory(); };
        window.addEventListener('flow_finished', onFlowFinished);
        return () => window.removeEventListener('flow_finished', onFlowFinished);
    }, [loadStatus, loadHistory, selectedExecId]);

    useEffect(() => {
        if (!isExecuting) return;
        const interval = setInterval(() => loadStatus(selectedExecId), 2000);
        return () => clearInterval(interval);
    }, [isExecuting, loadStatus, selectedExecId]);

    // ---- Actions ----
    const openExecuteModal = () => {
        // Clear first: keeping the previous fetch on screen while the new one is in flight
        // shows a host list that predates whatever discovery has run since.
        setHosts([]);
        loadHosts();
        setSelectedHostIds([]);
        setManualTargets('');
        setFlowVars({});
        setShowExecuteModal(true);
    };

    const handleExecute = async () => {
        try {
            setShowExecuteModal(false);
            setIsExecuting(true);
            toast.loading('Starting flow execution...');

            const targets = manualTargets
                .split('\n')
                .map(t => t.trim())
                .filter(Boolean);

            const payload = {
                variables: flowVars,
                host_ids: selectedHostIds,
                targets,
                host_id: selectedHostIds.length === 1 && targets.length === 0
                    ? selectedHostIds[0] : null,
                target: targets.length === 1 ? targets[0] : null,
            };

            const resp = await flowsApi.execute(flowId, payload);
            toast.dismiss();
            toast.success('Flow execution started');
            setSelectedExecId(resp.data?.id || null);
            loadStatus(resp.data?.id);
            loadHistory();
        } catch (error) {
            setIsExecuting(false);
            toast.dismiss();
            toast.error(error.response?.data?.detail || 'Failed to start flow');
            console.error(error);
        }
    };

    const handleStop = async () => {
        const execId = selectedExecId || flowStatus?.flow_execution_id;
        if (!execId) return;
        try {
            toast.loading('Stopping execution...');
            await flowsApi.stopExecution(flowId, execId);
            toast.dismiss();
            toast.success('Execution stopped');
            setIsExecuting(false);
            setIsPaused(false);
            loadStatus(execId);
            loadHistory();
        } catch (error) {
            toast.dismiss();
            toast.error('Failed to stop execution');
        }
    };

    const handlePause = async () => {
        const execId = selectedExecId || flowStatus?.flow_execution_id;
        if (!execId) return;
        try {
            toast.loading('Pausing execution...');
            await flowsApi.pauseExecution(flowId, execId);
            toast.dismiss();
            toast.success('Execution paused');
            setIsExecuting(false);
            setIsPaused(true);
            loadStatus(execId);
        } catch (error) {
            toast.dismiss();
            toast.error(error.response?.data?.detail || 'Failed to pause execution');
        }
    };

    const handleResume = async () => {
        const execId = selectedExecId || flowStatus?.flow_execution_id;
        if (!execId) return;
        try {
            toast.loading('Resuming execution...');
            await flowsApi.resumeExecution(flowId, execId);
            toast.dismiss();
            toast.success('Execution resumed');
            setIsPaused(false);
            setIsExecuting(true);
            loadStatus(execId);
        } catch (error) {
            toast.dismiss();
            toast.error(error.response?.data?.detail || 'Failed to resume execution');
        }
    };

    const selectHistoryItem = (execId) => {
        setSelectedExecId(execId);
        setShowHistory(false);
        loadStatus(execId);
    };

    const openOutputPanel = async (executionId, stepName, itemId, hostId) => {
        setOutputPanel({ executionId, stepName, itemId, hostId });
        setOutputData(null);
        setStepExecHistory([]);
        setOutputFullscreen(false);

        let resolvedExecId = executionId;

        // Load execution history for this checklist item
        let historyItems = [];
        if (itemId) {
            try {
                const resp = await executionsApi.list({ item_id: itemId, host_id: hostId || undefined, per_page: 50 });
                historyItems = resp.data?.items || resp.data || [];
                setStepExecHistory(historyItems);
            } catch { /* ignore */ }
        }

        // If no specific execution but history exists, auto-select latest
        if (!resolvedExecId && historyItems.length > 0) {
            resolvedExecId = historyItems[0].id;
            setOutputPanel(prev => ({ ...prev, executionId: resolvedExecId }));
        }

        if (resolvedExecId) {
            try {
                const resp = await executionsApi.get(resolvedExecId);
                setOutputData(resp.data);
            } catch { /* ignore */ }
        }
    };

    const handleSelectStepExecution = async (execId) => {
        if (!outputPanel) return;
        setOutputPanel(prev => ({ ...prev, executionId: execId }));
        try {
            const resp = await executionsApi.get(execId);
            setOutputData(resp.data);
        } catch {
            setOutputData(null);
        }
    };

    const handleDeleteStepExecution = async (execId) => {
        try {
            await executionsApi.delete(execId);
            setStepExecHistory(prev => prev.filter(e => e.id !== execId));
            if (outputPanel?.executionId === execId) {
                setOutputData(null);
            }
            toast.success('Execution deleted');
        } catch {
            toast.error('Failed to delete execution');
        }
    };

    const outputContent = outputData
        ? [outputData.stdout, outputData.stderr].filter(Boolean).join('\n') || null
        : null;

    const statusBadgeClass = (s) => {
        switch (s) {
            case 'running': return 'bg-blue-500/20 text-blue-400';
            case 'completed': return 'bg-green-500/20 text-green-400';
            case 'failed': return 'bg-red-500/20 text-red-400';
            case 'cancelled': return 'bg-yellow-500/20 text-yellow-400';
            case 'paused': return 'bg-purple-500/20 text-purple-400';
            default: return 'bg-gray-500/20 text-gray-400';
        }
    };

    // ---- Render ----
    return (
        <div className="flex flex-col h-[calc(100vh-100px)] w-full">
            {/* Header */}
            <div className="flex items-center justify-between px-4 py-3 bg-dark-800 border-b border-dark-700">
                <div className="flex items-center gap-4">
                    <button
                        onClick={() => navigate(-1)}
                        className="p-2 hover:bg-dark-700 rounded-lg text-dark-400 hover:text-dark-200"
                    >
                        <ArrowLeft size={20} />
                    </button>
                    <div>
                        <h2 className="text-lg font-semibold text-dark-100">
                            {flow?.name || 'Loading...'}
                        </h2>
                        <div className="flex items-center gap-2 text-sm">
                            <span className={`px-2 py-0.5 rounded text-xs font-medium ${statusBadgeClass(flowStatus?.overall_status)}`}>
                                {flowStatus?.overall_status || 'pending'}
                            </span>
                            {flowStatus?.targets?.length > 0 && (
                                <span className="text-dark-500 text-xs flex items-center gap-1">
                                    <Target size={10} /> {flowStatus.targets.length} targets
                                </span>
                            )}
                            {flowStatus?.flow_execution_id && (
                                <span className="text-dark-500 text-xs font-mono">
                                    {flowStatus.flow_execution_id.slice(0, 8)}...
                                </span>
                            )}
                        </div>
                    </div>
                </div>

                <div className="flex items-center gap-2">
                    {/* History */}
                    <div className="relative">
                        <button
                            onClick={() => { setShowHistory(!showHistory); loadHistory(); }}
                            className="p-2 hover:bg-dark-700 rounded-lg text-dark-400 hover:text-dark-200"
                            title="Execution history"
                        >
                            <History size={18} />
                        </button>
                        {showHistory && (
                            <div className="absolute right-0 top-full mt-1 w-80 bg-dark-800 border border-dark-600 rounded-lg shadow-xl z-50 max-h-64 overflow-y-auto">
                                {execHistory.length === 0 ? (
                                    <div className="p-3 text-dark-400 text-sm">No executions yet</div>
                                ) : execHistory.map(h => (
                                    <button
                                        key={h.id}
                                        onClick={() => selectHistoryItem(h.id)}
                                        className={`w-full text-left px-3 py-2 hover:bg-dark-700 border-b border-dark-700 last:border-0 ${
                                            selectedExecId === h.id ? 'bg-dark-700' : ''
                                        }`}
                                    >
                                        <div className="flex items-center justify-between">
                                            <span className={`text-xs font-medium px-1.5 py-0.5 rounded ${statusBadgeClass(h.status)}`}>
                                                {h.status}
                                            </span>
                                            <span className="text-dark-500 text-xs">
                                                {new Date(h.created_at).toLocaleString()}
                                            </span>
                                        </div>
                                        {h.targets?.length > 0 && (
                                            <div className="text-dark-400 text-xs mt-1 truncate">
                                                {h.targets.slice(0, 3).join(', ')}{h.targets.length > 3 ? ` +${h.targets.length - 3}` : ''}
                                            </div>
                                        )}
                                    </button>
                                ))}
                            </div>
                        )}
                    </div>

                    <button
                        onClick={() => { loadFlow(); loadStatus(selectedExecId); loadHistory(); }}
                        className="p-2 hover:bg-dark-700 rounded-lg text-dark-400 hover:text-dark-200"
                        title="Refresh"
                    >
                        <RefreshCw size={18} />
                    </button>

                    {/* Tri-state controls: Play / Pause+Stop / Resume+Stop */}
                    {isExecuting ? (
                        <>
                            <button
                                onClick={handlePause}
                                className="flex items-center gap-2 px-3 py-2 bg-purple-600 hover:bg-purple-700 text-white rounded-lg"
                                title="Pause flow execution"
                            >
                                <Pause size={16} />
                                Pause
                            </button>
                            <button
                                onClick={handleStop}
                                className="flex items-center gap-2 px-3 py-2 bg-red-600 hover:bg-red-700 text-white rounded-lg"
                                title="Stop flow execution"
                            >
                                <Square size={16} />
                                Stop
                            </button>
                        </>
                    ) : isPaused ? (
                        <>
                            <button
                                onClick={handleResume}
                                className="flex items-center gap-2 px-3 py-2 bg-green-600 hover:bg-green-700 text-white rounded-lg"
                                title="Resume flow execution"
                            >
                                <Play size={16} />
                                Resume
                            </button>
                            <button
                                onClick={handleStop}
                                className="flex items-center gap-2 px-3 py-2 bg-red-600 hover:bg-red-700 text-white rounded-lg"
                                title="Stop flow execution"
                            >
                                <Square size={16} />
                                Stop
                            </button>
                        </>
                    ) : (
                        <button
                            onClick={openExecuteModal}
                            className="flex items-center gap-2 px-4 py-2 bg-green-600 hover:bg-green-700 text-white rounded-lg font-medium"
                        >
                            <Play size={16} />
                            Execute Flow
                        </button>
                    )}
                </div>
            </div>

            {/* Main area: Canvas + optional output panel */}
            <div className="flex-1 flex">
                <div className={`${outputPanel ? (outputFullscreen ? 'hidden' : 'w-2/3') : 'w-full'} bg-dark-900`}>
                    <ReactFlow
                        nodes={nodes}
                        edges={edges}
                        onNodesChange={onNodesChange}
                        onEdgesChange={onEdgesChange}
                        onNodeClick={handleNodeClick}
                        nodesDraggable={false}
                        nodesConnectable={false}
                        fitView
                        fitViewOptions={{ padding: 0.2 }}
                    >
                        <Background color="#374151" gap={20} />
                        <Controls showInteractive={false} />
                    </ReactFlow>
                </div>

                {/* Output side panel with full OutputViewer */}
                {outputPanel && (
                    <div className={`${outputFullscreen ? 'w-full' : 'w-1/3'} bg-dark-800 border-l border-dark-700 flex flex-col`}>
                        <div className="flex items-center justify-between px-4 py-2 border-b border-dark-700">
                            <div className="flex items-center gap-2 min-w-0">
                                <Terminal size={14} className="text-dark-400 shrink-0" />
                                <span className="text-sm font-medium text-dark-200 truncate">
                                    {outputPanel.stepName || `Execution #${outputPanel.executionId}`}
                                </span>
                                {outputData && (
                                    <span className={`px-1.5 py-0.5 text-[10px] rounded shrink-0 ${statusBadgeClass(outputData.status)}`}>
                                        {outputData.status}
                                    </span>
                                )}
                            </div>
                            <button onClick={() => { setOutputPanel(null); setOutputData(null); setStepExecHistory([]); setOutputFullscreen(false); }}
                                className="p-1 hover:bg-dark-700 rounded text-dark-400">
                                <X size={14} />
                            </button>
                        </div>
                        <div className="flex-1 overflow-hidden">
                            <OutputViewer
                                content={outputContent}
                                command={outputData?.command}
                                executionHistory={stepExecHistory}
                                selectedExecutionId={outputPanel.executionId}
                                onSelectExecution={handleSelectStepExecution}
                                onDeleteExecution={handleDeleteStepExecution}
                                isFullscreen={outputFullscreen}
                                onToggleFullscreen={() => setOutputFullscreen(prev => !prev)}
                            />
                        </div>
                    </div>
                )}
            </div>

            {/* Execute Modal */}
            {showExecuteModal && (
                <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
                    <div className="bg-dark-800 rounded-xl w-[560px] max-h-[80vh] overflow-y-auto border border-dark-600 shadow-2xl">
                        <div className="flex items-center justify-between px-5 py-4 border-b border-dark-700">
                            <h3 className="text-lg font-semibold text-dark-100">Execute Flow</h3>
                            <button onClick={() => setShowExecuteModal(false)}
                                className="p-1 hover:bg-dark-700 rounded text-dark-400">
                                <X size={18} />
                            </button>
                        </div>
                        <div className="px-5 py-4 space-y-5">
                            {/* Host selection */}
                            {hosts.length > 0 && (
                                <div>
                                    <label className="block text-sm font-medium text-dark-300 mb-2">
                                        Select Hosts
                                    </label>
                                    <div className="max-h-40 overflow-y-auto bg-dark-900 rounded-lg border border-dark-700 p-2 space-y-1">
                                        {hosts.map(h => (
                                            <label key={h.id} className="flex items-center gap-2 px-2 py-1.5 hover:bg-dark-800 rounded cursor-pointer">
                                                <input
                                                    type="checkbox"
                                                    checked={selectedHostIds.includes(h.id)}
                                                    onChange={e => {
                                                        if (e.target.checked) {
                                                            setSelectedHostIds(prev => [...prev, h.id]);
                                                        } else {
                                                            setSelectedHostIds(prev => prev.filter(id => id !== h.id));
                                                        }
                                                    }}
                                                    className="rounded border-dark-600 bg-dark-800 text-blue-500"
                                                />
                                                <span className="text-sm text-dark-200">{h.ip_address}</span>
                                                {h.hostname && (
                                                    <span className="text-xs text-dark-400">({h.hostname})</span>
                                                )}
                                                {h.tags?.length > 0 && h.tags.map(t => (
                                                    <span key={t} className="text-[10px] px-1 py-0.5 bg-dark-700 text-dark-400 rounded">{t}</span>
                                                ))}
                                            </label>
                                        ))}
                                    </div>
                                </div>
                            )}

                            {/* Manual targets */}
                            <div>
                                <label className="block text-sm font-medium text-dark-300 mb-2">
                                    Manual Targets (one per line)
                                </label>
                                <textarea
                                    value={manualTargets}
                                    onChange={e => setManualTargets(e.target.value)}
                                    placeholder="10.10.10.1&#10;10.10.10.2&#10;example.com"
                                    rows={4}
                                    className="w-full bg-dark-900 border border-dark-700 rounded-lg px-3 py-2 text-sm text-dark-200 placeholder-dark-500 font-mono focus:outline-none focus:ring-1 focus:ring-blue-500"
                                />
                            </div>

                            {/* Variables */}
                            <div>
                                <label className="block text-sm font-medium text-dark-300 mb-2">
                                    Variables (JSON)
                                </label>
                                <textarea
                                    value={JSON.stringify(flowVars, null, 2)}
                                    onChange={e => {
                                        try { setFlowVars(JSON.parse(e.target.value)); } catch { /* ignore */ }
                                    }}
                                    rows={3}
                                    className="w-full bg-dark-900 border border-dark-700 rounded-lg px-3 py-2 text-sm text-dark-200 font-mono focus:outline-none focus:ring-1 focus:ring-blue-500"
                                />
                            </div>
                        </div>
                        <div className="flex justify-end gap-3 px-5 py-4 border-t border-dark-700">
                            <button
                                onClick={() => setShowExecuteModal(false)}
                                className="px-4 py-2 text-dark-300 hover:text-dark-100 rounded-lg"
                            >
                                Cancel
                            </button>
                            <button
                                onClick={handleExecute}
                                className="px-5 py-2 bg-green-600 hover:bg-green-700 text-white rounded-lg font-medium flex items-center gap-2"
                            >
                                <Play size={16} />
                                Execute
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
