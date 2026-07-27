import { useState, useCallback, useEffect, useMemo, useRef } from 'react';
import ReactFlow, {
    Background,
    Controls,
    addEdge,
    getConnectedEdges,
    useNodesState,
    useEdgesState,
    MarkerType
} from 'reactflow';
import 'reactflow/dist/style.css';
import { useParams } from 'react-router-dom';
import { flowsApi, checklistsApi } from '../services/api';
import toast from 'react-hot-toast';
import { Plus, ChevronRight, ChevronDown, Save, X, ArrowRight, Trash2 } from 'lucide-react';

export default function FlowEditor() {
    const { flowId } = useParams();
    const [nodes, setNodes, onNodesChange] = useNodesState([]);
    const [edges, setEdges, onEdgesChange] = useEdgesState([]);
    const [flow, setFlow] = useState(null);
    const [checklistTemplates, setChecklistTemplates] = useState([]);
    const [expandedGroups, setExpandedGroups] = useState({});
    const [isSaving, setIsSaving] = useState(false);
    const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false);
    const [selectedElements, setSelectedElements] = useState({ nodes: [], edges: [] });

    // Edge mapping panel state
    const [selectedEdge, setSelectedEdge] = useState(null);
    const [edgeMappings, setEdgeMappings] = useState({}); // { "source-target": { targetVar: sourceVar } }

    // Step item data for variable info
    const [stepItems, setStepItems] = useState({}); // orderIndex -> ChecklistItem data

    // Refs so save handler always reads latest state (avoids stale closure when user adds mapping then immediately saves)
    const nodesRef = useRef(nodes);
    const edgesRef = useRef(edges);
    const edgeMappingsRef = useRef(edgeMappings);
    const flowRef = useRef(flow);
    nodesRef.current = nodes;
    edgesRef.current = edges;
    edgeMappingsRef.current = edgeMappings;
    flowRef.current = flow;

    // Initial load
    useEffect(() => {
        loadFlow();
        loadChecklistTemplates();
    }, [flowId]);

    const loadFlow = async () => {
        try {
            const response = await flowsApi.get(flowId);
            const flowData = response.data;
            setFlow(flowData);

            // Convert steps to nodes/edges
            const newNodes = [];
            const newEdges = [];
            const newStepItems = {};
            const newEdgeMappings = {};

            // Allow manual layout or simple auto-layout
            let y = 50;
            const x = 250;

            // First pass: Create nodes and build order_index mapping
            const orderIndexToNodeIds = {}; // { orderIndex: [nodeId, ...] }

            flowData.steps.forEach((step, index) => {
                const nodeId = index.toString();
                const orderIdx = step.order_index;

                if (!orderIndexToNodeIds[orderIdx]) {
                    orderIndexToNodeIds[orderIdx] = [];
                }
                orderIndexToNodeIds[orderIdx].push(nodeId);

                // Store full step data for variable mapping
                newStepItems[nodeId] = {
                    id: step.checklist_item_id,
                    name: step.item_name || 'Unknown Task',
                    command: step.item_command || '',
                    input_mapping: step.input_mapping || {},
                    // Extract variables from command template
                    variables: extractVariablesFromCommand(step.item_command || ''),
                    // Output variables from output_regex keys would come from item data
                    outputs: step.output_regex_keys || []
                };

                // Node - show name and truncated command
                const commandPreview = (step.item_command || '').length > 50
                    ? step.item_command.substring(0, 50) + '...'
                    : step.item_command || '';

                newNodes.push({
                    id: nodeId,
                    position: (step.ui_position && step.ui_position.x !== undefined) ? step.ui_position : { x, y },
                    data: {
                        label: (
                            <div className="text-center">
                                <div className="font-semibold">{step.item_name || 'Unknown Task'}</div>
                                <div className="text-xs text-dark-400 mt-1 font-mono break-all">{commandPreview}</div>
                            </div>
                        ),
                        step: step
                    },
                    type: 'default',
                    style: {
                        background: '#1f2937',
                        color: '#fff',
                        border: '1px solid #374151',
                        borderRadius: '8px',
                        padding: '10px',
                        width: '250px',
                        minHeight: '60px'
                    }
                });

                // Only increment Y if we don't have saved positions (legacy layout)
                if (!step.ui_position || step.ui_position.x === undefined) {
                    y += 150;
                }
            });

            // Second pass: Create edges using updated Node IDs
            flowData.steps.forEach((step, index) => {
                const targetNodeId = index.toString();

                Object.entries(step.input_mapping || {}).forEach(([targetVar, mapping]) => {
                    // Handle both string and object formats
                    const sourceExpr = typeof mapping === 'string' ? mapping : (mapping.source || '');
                    const parser = typeof mapping === 'object' ? mapping.parser : null;

                    const matches = sourceExpr.match(/\{(\d+)\.(\w+)\}/);
                    if (matches && matches[1] && matches[2]) {
                        const sourceOrderIndex = matches[1];
                        const sourceVar = matches[2];

                        // Find all source nodes matching this order index
                        const sourceNodeIds = orderIndexToNodeIds[sourceOrderIndex] || [];

                        sourceNodeIds.forEach(sourceNodeId => {
                            const edgeId = `e${sourceNodeId}-${targetNodeId}`;

                            // Only add edge once per source-target pair
                            if (!newEdges.find(e => e.id === edgeId)) {
                                newEdges.push({
                                    id: edgeId,
                                    source: sourceNodeId,
                                    target: targetNodeId,
                                    animated: true,
                                    markerEnd: { type: MarkerType.ArrowClosed },
                                    style: { stroke: '#4B5563', strokeWidth: 2 }
                                });
                            }

                            // Store mapping with parser info
                            if (!newEdgeMappings[edgeId]) {
                                newEdgeMappings[edgeId] = {};
                            }
                            newEdgeMappings[edgeId][targetVar] = { source: sourceVar, parser: parser };
                        });
                    }
                });
            });

            // Third pass: Restore visual edges from flow_definition.edges (connections without variable mappings)
            const savedEdges = flowData.flow_definition?.edges;
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
                                    animated: true,
                                    markerEnd: { type: MarkerType.ArrowClosed },
                                    style: { stroke: '#4B5563', strokeWidth: 2 }
                                });
                            }
                        });
                    });
                });
            }

            setNodes(newNodes);
            setEdges(newEdges);

            setStepItems(newStepItems);
            setEdgeMappings(newEdgeMappings);
            setHasUnsavedChanges(false);

        } catch (error) {
            console.error(error);
            toast.error('Failed to load flow');
        }
    };

    // Extract variable placeholders from command template
    const extractVariablesFromCommand = (command) => {
        const matches = command.match(/\{(\w+)\}/g) || [];
        return [...new Set(matches.map(m => m.replace(/[{}]/g, '')))];
    };

    const loadChecklistTemplates = async () => {
        try {
            const response = await checklistsApi.listGroups(null, true);
            setChecklistTemplates(response.data);
        } catch (error) {
            console.error(error);
            toast.error('Failed to load checklist templates');
        }
    };

    const toggleGroup = (groupId) => {
        setExpandedGroups(prev => ({
            ...prev,
            [groupId]: !prev[groupId]
        }));
    };

    const handleAddStep = async (item) => {
        if (!flow) return;

        const currentMaxOrder = flow.steps.length > 0
            ? Math.max(...flow.steps.map(s => s.order_index))
            : -1;
        const newOrderIndex = currentMaxOrder + 1;

        const newStep = {
            checklist_item_id: item.id,
            order_index: newOrderIndex,
            input_mapping: {},
            condition: null,
            on_failure: 'stop'
        };

        try {
            const updatedSteps = [...flow.steps, newStep];
            await flowsApi.update(flow.id, { steps: updatedSteps });
            toast.success('Step added');
            loadFlow();
        } catch (error) {
            console.error(error);
            toast.error('Failed to add step');
        }
    };

    const onSelectionChange = useCallback(({ nodes, edges }) => {
        setSelectedElements({ nodes, edges });
    }, []);

    const handleDelete = useCallback(() => {
        const { nodes: selectedNodes, edges: selectedEdges } = selectedElements;
        if (selectedNodes.length === 0 && selectedEdges.length === 0) return;

        // Find all edges connected to selected nodes (they must also be deleted)
        const connectedEdges = getConnectedEdges(selectedNodes, edges);
        const edgesToDelete = [...selectedEdges, ...connectedEdges];
        const edgeIdsToDelete = new Set(edgesToDelete.map(e => e.id));

        // Update nodes state
        const nodeIdsToDelete = new Set(selectedNodes.map(n => n.id));
        setNodes((nds) => nds.filter((n) => !nodeIdsToDelete.has(n.id)));

        // Update edges state
        setEdges((eds) => eds.filter((e) => !edgeIdsToDelete.has(e.id)));

        // Clean up mappings for deleted edges
        setEdgeMappings(prev => {
            const updated = { ...prev };
            edgeIdsToDelete.forEach(edgeId => {
                delete updated[edgeId];
            });
            return updated;
        });

        // Clear selection
        setSelectedElements({ nodes: [], edges: [] });
        setSelectedEdge(null);
        setHasUnsavedChanges(true);
        toast.success(`Deleted ${selectedNodes.length} steps and ${edgesToDelete.length} connections`);
    }, [selectedElements, edges, setNodes, setEdges]);

    const onConnect = useCallback((params) => {
        const newEdge = {
            ...params,
            id: `e${params.source}-${params.target}`,
            animated: true,
            markerEnd: { type: MarkerType.ArrowClosed },
            style: { stroke: '#4B5563', strokeWidth: 2 }
        };
        setEdges((eds) => addEdge(newEdge, eds));
        setHasUnsavedChanges(true);

        // Initialize empty mapping for new edge
        setEdgeMappings(prev => ({
            ...prev,
            [newEdge.id]: {}
        }));
    }, [setEdges]);

    const onEdgeClick = useCallback((event, edge) => {
        setSelectedEdge(edge);
    }, []);

    // Topological sort to determine execution order (parallel execution support)
    const getTopologicalOrder = (nodes, edges) => {
        // Build adjacency list and in-degree map
        const adj = {};
        const inDegree = {};

        nodes.forEach(node => {
            adj[node.id] = [];
            inDegree[node.id] = 0;
        });

        edges.forEach(edge => {
            if (adj[edge.source]) {
                adj[edge.source].push(edge.target);
                inDegree[edge.target] = (inDegree[edge.target] || 0) + 1;
            }
        });

        // Queue for nodes with in-degree 0
        let queue = [];
        nodes.forEach(node => {
            if (inDegree[node.id] === 0) {
                queue.push({ id: node.id, depth: 0 });
            }
        });

        const nodeDepths = {}; // id -> depth

        while (queue.length > 0) {
            const { id, depth } = queue.shift();
            nodeDepths[id] = depth;

            if (adj[id]) {
                adj[id].forEach(neighbor => {
                    inDegree[neighbor]--;
                    if (inDegree[neighbor] === 0) {
                        queue.push({ id: neighbor, depth: depth + 1 });
                    }
                });
            }
        }

        // Handle cycles or disconnected nodes (fallback to sequential based on current order if needed, but depth is better)
        // If nodes remain with in-degree > 0, there is a cycle. For now, we assume DAG.
        // Assign unvisited nodes a depth based on their current y position? Or just max depth + 1.

        return nodeDepths;
    };

    // Save flow with all edges and mappings
    const handleSaveFlow = async () => {
        const currentFlow = flowRef.current;
        const currentNodes = nodesRef.current;
        const currentEdges = edgesRef.current;
        const currentEdgeMappings = edgeMappingsRef.current;
        if (!currentFlow) return;
        setIsSaving(true);

        try {
            // Calculate topological depth for order_index (use latest nodes/edges from refs)
            const nodeDepths = getTopologicalOrder(currentNodes, currentEdges);

            // Build updated steps with input_mapping from edgeMappings (use ref so recently added mappings are included)
            const updatedSteps = currentNodes.map(node => {
                const step = node.data.step;
                const nodeId = node.id;

                // Determine order_index of source node
                // Note: edge.source refers to Node ID (index), but we need Order Index for mapping {order.var}
                // But FlowManager expects {order.var}.
                // So when saving, we need to map Node ID -> New Order Index.

                const newInputMapping = {};

                // Find all edges targeting this step
                currentEdges.forEach(edge => {
                    if (edge.target === nodeId) {
                        const mappings = currentEdgeMappings[edge.id] || {};

                        // We need the Source Node's NEW order index
                        const sourceNodeId = edge.source;
                        // Use the calculated depth for the source node
                        const sourceOrderIndex = nodeDepths[sourceNodeId] !== undefined ? nodeDepths[sourceNodeId] : 0;

                        Object.entries(mappings).forEach(([targetVar, mappingData]) => {
                            // mappingData is {source: "varName", parser: "regex" || null}
                            const sourceVar = typeof mappingData === 'string' ? mappingData : mappingData.source;
                            const parser = typeof mappingData === 'object' ? mappingData.parser : null;

                            // Construct mapping with Order Index
                            const mappingString = `{${sourceOrderIndex}.${sourceVar}}`;

                            if (parser) {
                                // Use object format with parser
                                newInputMapping[targetVar] = {
                                    source: mappingString,
                                    parser: parser
                                };
                            } else {
                                // Use simple string format
                                newInputMapping[targetVar] = mappingString;
                            }
                        });
                    }
                });

                // Use calculated depth as order_index
                const newOrderIndex = nodeDepths[node.id] !== undefined ? nodeDepths[node.id] : 999;

                return {
                    checklist_item_id: step.checklist_item_id,
                    order_index: newOrderIndex,
                    input_mapping: newInputMapping,
                    condition: step.condition,
                    on_failure: step.on_failure || 'stop',
                    timeout_override: step.timeout_override,
                    target_mode: step.target_mode || 'inherit',
                    target_filter: step.target_filter || {},
                    ui_position: node.position,
                };
            });



            // Sort steps by order_index for clean storage look
            updatedSteps.sort((a, b) => a.order_index - b.order_index);

            // Persist visual edges so connections without variable mappings are restored on load
            const builtEdges = currentEdges
                .filter(e => nodeDepths[e.source] !== undefined && nodeDepths[e.target] !== undefined)
                .map(e => ({ source: nodeDepths[e.source], target: nodeDepths[e.target] }));

            const flowDefinition = { ...(currentFlow.flow_definition || {}), edges: builtEdges };

            await flowsApi.update(currentFlow.id, { steps: updatedSteps, flow_definition: flowDefinition });
            toast.success('Flow saved');
            setHasUnsavedChanges(false);
            loadFlow(); // Refresh to sync
        } catch (error) {
            console.error(error);
            toast.error('Failed to save flow');
        } finally {
            setIsSaving(false);
        }
    };

    // Update mapping for an edge (now includes parser)
    const updateEdgeMapping = (edgeId, targetVar, sourceVar, parser = null) => {
        setEdgeMappings(prev => ({
            ...prev,
            [edgeId]: {
                ...(prev[edgeId] || {}),
                [targetVar]: { source: sourceVar, parser: parser || null }
            }
        }));
        setHasUnsavedChanges(true);
    };

    // Remove a mapping
    const removeEdgeMapping = (edgeId, targetVar) => {
        setEdgeMappings(prev => {
            const updated = { ...prev };
            if (updated[edgeId]) {
                delete updated[edgeId][targetVar];
            }
            return updated;
        });
        setHasUnsavedChanges(true);
    };

    // Get source step's available outputs (from output_regex keys defined in the checklist item)
    const getSourceOutputs = (sourceId) => {
        const stepItem = stepItems[sourceId];
        if (!stepItem) return [];

        // Only show outputs actually defined in output_regex for this item
        const outputs = [...(stepItem.outputs || [])];

        // Always include 'stdout' for raw output access
        if (!outputs.includes('stdout')) {
            outputs.unshift('stdout');
        }

        return outputs;
    };

    // Get target step's input variables
    const getTargetInputs = (targetId) => {
        const stepItem = stepItems[targetId];
        if (!stepItem) return [];
        return stepItem.variables || [];
    };

    return (
        <div className="flex h-[calc(100vh-100px)] w-full gap-4">
            {/* Sidebar */}
            <div className="w-80 bg-dark-900 border border-dark-700 rounded-lg overflow-hidden flex flex-col">
                <div className="p-4 border-b border-dark-700 bg-dark-800">
                    <h3 className="font-semibold text-dark-100">Add Steps</h3>
                </div>
                <div className="flex-1 overflow-y-auto p-2 space-y-2">
                    {checklistTemplates.map(group => (
                        <div key={group.id} className="border border-dark-700 rounded-lg overflow-hidden">
                            <button
                                onClick={() => toggleGroup(group.id)}
                                className="w-full px-3 py-2 bg-dark-800 flex items-center justify-between text-left hover:bg-dark-700 transition-colors"
                            >
                                <span className="font-medium text-sm text-dark-200">{group.name}</span>
                                {expandedGroups[group.id] ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                            </button>

                            {expandedGroups[group.id] && (
                                <div className="bg-dark-900 p-2 space-y-1">
                                    {group.items?.map(item => (
                                        <div key={item.id} className="flex items-center justify-between p-2 rounded hover:bg-dark-800 group">
                                            <div className="text-sm text-dark-300 truncate pr-2" title={item.name}>
                                                {item.name}
                                            </div>
                                            <button
                                                onClick={() => handleAddStep(item)}
                                                className="p-1 text-accent-primary hover:bg-accent-primary/10 rounded opacity-0 group-hover:opacity-100 transition-all"
                                                title="Add to Flow"
                                            >
                                                <Plus size={14} />
                                            </button>
                                        </div>
                                    ))}
                                    {(!group.items || group.items.length === 0) && (
                                        <div className="text-xs text-dark-500 p-2 text-center">No items</div>
                                    )}
                                </div>
                            )}
                        </div>
                    ))}
                    {checklistTemplates.length === 0 && (
                        <div className="text-center py-8 text-dark-500 text-sm">
                            No checklist templates found.
                        </div>
                    )}
                </div>
            </div>

            {/* Main Canvas */}
            <div className="flex-1 bg-dark-900 border border-dark-700 rounded-lg overflow-hidden flex flex-col">
                <div className="p-4 border-b border-dark-700 flex justify-between items-center bg-dark-800">
                    <h2 className="text-lg font-semibold text-dark-100">
                        {flow?.name || 'Loading...'}
                        {hasUnsavedChanges && <span className="ml-2 text-yellow-500 text-sm">●</span>}
                    </h2>
                    <div className="flex gap-2">
                        <button
                            className="btn-secondary"
                            onClick={loadFlow}
                        >
                            Refresh
                        </button>
                        {(selectedElements.nodes.length > 0 || selectedElements.edges.length > 0) && (
                            <button
                                className="btn-secondary text-red-400 hover:text-red-300 hover:bg-red-900/20 flex items-center gap-2"
                                onClick={handleDelete}
                                title="Delete selected items"
                            >
                                <Trash2 size={16} />
                                Delete ({selectedElements.nodes.length + selectedElements.edges.length})
                            </button>
                        )}
                        <button
                            className={`btn-primary flex items-center gap-2 ${isSaving ? 'opacity-50 cursor-not-allowed' : ''}`}
                            onClick={handleSaveFlow}
                            disabled={isSaving}
                        >
                            <Save size={16} />
                            {isSaving ? 'Saving...' : 'Save Flow'}
                        </button>
                    </div>
                </div>
                <div className="flex-1 relative">
                    <ReactFlow
                        nodes={nodes}
                        edges={edges}
                        onNodesChange={(changes) => {
                            onNodesChange(changes);
                            // Detect if any change is a position change (dragging) or selection change
                            // Selection changes are handled by onSelectionChange, but dragging needs to mark unsaved.
                            if (changes.some(c => c.type === 'position' || c.type === 'add' || c.type === 'remove')) {
                                setHasUnsavedChanges(true);
                            }
                        }}
                        onEdgesChange={(changes) => {
                            onEdgesChange(changes);
                            setHasUnsavedChanges(true);
                        }}
                        onConnect={onConnect}
                        onEdgeClick={onEdgeClick}
                        onSelectionChange={onSelectionChange}
                        deleteKeyCode={['Backspace', 'Delete']}
                        fitView
                    >
                        <Background color="#374151" gap={16} />
                        <Controls className="bg-dark-800 text-dark-100 border-dark-700" />
                    </ReactFlow>

                    {/* Step Properties Panel */}
                    {selectedElements.nodes.length === 1 && !selectedEdge && (() => {
                        const selNode = nodes.find(n => n.id === selectedElements.nodes[0]?.id);
                        const step = selNode?.data?.step;
                        if (!step) return null;
                        return (
                            <div className="absolute top-4 right-4 w-72 bg-dark-800 border border-dark-700 rounded-lg shadow-xl z-50">
                                <div className="p-3 border-b border-dark-700">
                                    <h3 className="font-semibold text-dark-100 text-sm">Step Properties</h3>
                                    <div className="text-xs text-dark-400 mt-1">{step.item_name}</div>
                                </div>
                                <div className="p-3 space-y-3">
                                    <div>
                                        <label className="text-xs font-medium text-dark-400 block mb-1">Target Mode</label>
                                        <select
                                            value={step.target_mode || 'inherit'}
                                            onChange={e => {
                                                const newMode = e.target.value;
                                                setNodes(nds => nds.map(n => {
                                                    if (n.id !== selNode.id) return n;
                                                    return { ...n, data: { ...n.data, step: { ...n.data.step, target_mode: newMode } } };
                                                }));
                                                setHasUnsavedChanges(true);
                                            }}
                                            className="w-full bg-dark-700 border border-dark-600 rounded px-2 py-1.5 text-xs text-dark-200"
                                        >
                                            <option value="inherit">Inherit (from flow)</option>
                                            <option value="single">Single Host</option>
                                            <option value="all">All Project Hosts</option>
                                            <option value="filtered">Filtered by Tags</option>
                                        </select>
                                    </div>
                                    {(step.target_mode === 'filtered') && (
                                        <div>
                                            <label className="text-xs font-medium text-dark-400 block mb-1">Filter Tags (comma-separated)</label>
                                            <input
                                                type="text"
                                                value={(step.target_filter?.tags || []).join(', ')}
                                                onChange={e => {
                                                    const tags = e.target.value.split(',').map(t => t.trim()).filter(Boolean);
                                                    setNodes(nds => nds.map(n => {
                                                        if (n.id !== selNode.id) return n;
                                                        return { ...n, data: { ...n.data, step: { ...n.data.step, target_filter: { tags } } } };
                                                    }));
                                                    setHasUnsavedChanges(true);
                                                }}
                                                className="w-full bg-dark-700 border border-dark-600 rounded px-2 py-1.5 text-xs text-dark-200 font-mono"
                                                placeholder="e.g. web, linux"
                                            />
                                        </div>
                                    )}
                                    <div>
                                        <label className="text-xs font-medium text-dark-400 block mb-1">On Failure</label>
                                        <select
                                            value={step.on_failure || 'stop'}
                                            onChange={e => {
                                                const val = e.target.value;
                                                setNodes(nds => nds.map(n => {
                                                    if (n.id !== selNode.id) return n;
                                                    return { ...n, data: { ...n.data, step: { ...n.data.step, on_failure: val } } };
                                                }));
                                                setHasUnsavedChanges(true);
                                            }}
                                            className="w-full bg-dark-700 border border-dark-600 rounded px-2 py-1.5 text-xs text-dark-200"
                                        >
                                            <option value="stop">Stop</option>
                                            <option value="continue">Continue</option>
                                            <option value="skip_remaining">Skip Remaining</option>
                                        </select>
                                    </div>
                                    <div>
                                        <label className="text-xs font-medium text-dark-400 block mb-1">Condition</label>
                                        <input
                                            type="text"
                                            value={step.condition || ''}
                                            onChange={e => {
                                                const val = e.target.value;
                                                setNodes(nds => nds.map(n => {
                                                    if (n.id !== selNode.id) return n;
                                                    return { ...n, data: { ...n.data, step: { ...n.data.step, condition: val || null } } };
                                                }));
                                                setHasUnsavedChanges(true);
                                            }}
                                            className="w-full bg-dark-700 border border-dark-600 rounded px-2 py-1.5 text-xs text-dark-200 font-mono"
                                            placeholder='e.g. "{0.exit_code}" == "0"'
                                        />
                                    </div>
                                    <div>
                                        <label className="text-xs font-medium text-dark-400 block mb-1">Timeout (seconds)</label>
                                        <input
                                            type="number"
                                            value={step.timeout_override || ''}
                                            onChange={e => {
                                                const val = e.target.value ? parseInt(e.target.value) : null;
                                                setNodes(nds => nds.map(n => {
                                                    if (n.id !== selNode.id) return n;
                                                    return { ...n, data: { ...n.data, step: { ...n.data.step, timeout_override: val } } };
                                                }));
                                                setHasUnsavedChanges(true);
                                            }}
                                            className="w-full bg-dark-700 border border-dark-600 rounded px-2 py-1.5 text-xs text-dark-200"
                                            placeholder="Default"
                                        />
                                    </div>
                                </div>
                            </div>
                        );
                    })()}

                    {/* Parameter Mapping Panel */}
                    {selectedEdge && (
                        <div className="absolute top-4 right-4 w-80 bg-dark-800 border border-dark-700 rounded-lg shadow-xl z-50">
                            <div className="p-3 border-b border-dark-700 flex justify-between items-center">
                                <h3 className="font-semibold text-dark-100 text-sm">
                                    Variable Mapping
                                </h3>
                                <button
                                    onClick={() => setSelectedEdge(null)}
                                    className="p-1 hover:bg-dark-700 rounded"
                                >
                                    <X size={16} />
                                </button>
                            </div>

                            <div className="p-3 text-xs text-dark-400 border-b border-dark-700">
                                Step {selectedEdge.source} → Step {selectedEdge.target}
                            </div>

                            <div className="p-3 space-y-3 max-h-80 overflow-y-auto">
                                {/* Source Outputs */}
                                <div>
                                    <div className="text-xs font-medium text-dark-400 mb-2">
                                        Source Outputs (Step {selectedEdge.source})
                                    </div>
                                    <div className="flex flex-wrap gap-1">
                                        {getSourceOutputs(selectedEdge.source).map(output => (
                                            <span
                                                key={output}
                                                className="px-2 py-1 bg-green-900/30 text-green-400 rounded text-xs cursor-pointer hover:bg-green-900/50"
                                                onClick={() => {
                                                    // Copy to clipboard or select for mapping
                                                    navigator.clipboard.writeText(output);
                                                    toast.success(`Copied: ${output}`);
                                                }}
                                            >
                                                {output}
                                            </span>
                                        ))}
                                    </div>
                                </div>

                                {/* Target Inputs */}
                                <div>
                                    <div className="text-xs font-medium text-dark-400 mb-2">
                                        Target Inputs (Step {selectedEdge.target})
                                    </div>
                                    <div className="flex flex-wrap gap-1">
                                        {getTargetInputs(selectedEdge.target).map(input => (
                                            <span
                                                key={input}
                                                className="px-2 py-1 bg-blue-900/30 text-blue-400 rounded text-xs"
                                            >
                                                {input}
                                            </span>
                                        ))}
                                    </div>
                                </div>

                                {/* Current Mappings */}
                                <div>
                                    <div className="text-xs font-medium text-dark-400 mb-2">
                                        Current Mappings
                                    </div>
                                    {Object.entries(edgeMappings[selectedEdge.id] || {}).length > 0 ? (
                                        <div className="space-y-2">
                                            {Object.entries(edgeMappings[selectedEdge.id] || {}).map(([target, mappingData]) => {
                                                const source = typeof mappingData === 'string' ? mappingData : mappingData.source;
                                                const parser = typeof mappingData === 'object' ? mappingData.parser : null;
                                                return (
                                                    <div key={target} className="bg-dark-700 rounded p-2">
                                                        <div className="flex items-center justify-between">
                                                            <div className="flex items-center gap-2 text-xs">
                                                                <span className="text-green-400">{source}</span>
                                                                <ArrowRight size={12} className="text-dark-500" />
                                                                <span className="text-blue-400">{target}</span>
                                                            </div>
                                                            <button
                                                                onClick={() => removeEdgeMapping(selectedEdge.id, target)}
                                                                className="p-1 hover:bg-dark-600 rounded text-red-400"
                                                            >
                                                                <Trash2 size={12} />
                                                            </button>
                                                        </div>
                                                        {parser && (
                                                            <div className="mt-1 text-xs text-yellow-400/80 font-mono truncate" title={parser}>
                                                                Parser: {parser.length > 30 ? parser.slice(0, 30) + '...' : parser}
                                                            </div>
                                                        )}
                                                    </div>
                                                );
                                            })}
                                        </div>
                                    ) : (
                                        <div className="text-xs text-dark-500 italic">No mappings</div>
                                    )}
                                </div>

                                {/* Add Mapping Form */}
                                <div className="border-t border-dark-700 pt-3">
                                    <div className="text-xs font-medium text-dark-400 mb-2">Add Mapping</div>
                                    <div className="flex gap-2">
                                        <select
                                            id="sourceVar"
                                            className="flex-1 bg-dark-700 border border-dark-600 rounded px-2 py-1 text-xs text-dark-200"
                                            defaultValue=""
                                        >
                                            <option value="" disabled>Source output</option>
                                            {getSourceOutputs(selectedEdge.source).map(o => (
                                                <option key={o} value={o}>{o}</option>
                                            ))}
                                        </select>
                                        <ArrowRight size={16} className="text-dark-500 flex-shrink-0 self-center" />
                                        <select
                                            id="targetVar"
                                            className="flex-1 bg-dark-700 border border-dark-600 rounded px-2 py-1 text-xs text-dark-200"
                                            defaultValue=""
                                        >
                                            <option value="" disabled>Target input</option>
                                            {getTargetInputs(selectedEdge.target).map(i => (
                                                <option key={i} value={i}>{i}</option>
                                            ))}
                                        </select>
                                    </div>
                                    <div className="mt-2">
                                        <input
                                            id="parserRegex"
                                            type="text"
                                            placeholder="Parser regex (optional) e.g. \d+\.\d+\.\d+\.\d+"
                                            className="w-full bg-dark-700 border border-dark-600 rounded px-2 py-1 text-xs text-dark-200 font-mono"
                                        />
                                        <div className="text-xs text-dark-500 mt-1">Extract/transform output before passing to input</div>
                                    </div>
                                    <button
                                        onClick={() => {
                                            const sourceEl = document.getElementById('sourceVar');
                                            const targetEl = document.getElementById('targetVar');
                                            const parserEl = document.getElementById('parserRegex');
                                            if (sourceEl.value && targetEl.value) {
                                                updateEdgeMapping(
                                                    selectedEdge.id,
                                                    targetEl.value,
                                                    sourceEl.value,
                                                    parserEl.value || null
                                                );
                                                sourceEl.value = '';
                                                targetEl.value = '';
                                                parserEl.value = '';
                                            }
                                        }}
                                        className="mt-2 w-full px-3 py-1.5 bg-accent-primary text-white rounded text-xs hover:bg-accent-primary/80"
                                    >
                                        Add Mapping
                                    </button>
                                </div>
                            </div>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
