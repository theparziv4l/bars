import { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { Plus, ArrowLeft, MoreVertical, Play, Edit2, Trash2 } from 'lucide-react'
import { checklistsApi } from '../services/api'
import ChecklistItemForm from '../components/ChecklistItemForm'
import toast from 'react-hot-toast'

export default function ChecklistGroupDetail() {
    const { id } = useParams()
    const navigate = useNavigate()

    // State
    const [group, setGroup] = useState(null)
    const [items, setItems] = useState([])
    const [loading, setLoading] = useState(true)
    const [editingItem, setEditingItem] = useState(null) // item object or 'new'

    useEffect(() => {
        fetchData()
    }, [id])

    const fetchData = async () => {
        try {
            setLoading(true)
            // Ideally we get group details + items. The listGroups API returns items nested.
            // But we might need a specific getGroup API if the list is huge.
            // For now, let's filter from listGroups or implement getGroup in backend if needed.
            // Wait, I implemented getGroup in backend? Let's check api/checklists.py. 
            // It doesn't seem to have a direct getGroup endpoint in the file view from earlier, 
            // but the listGroups returns items. 
            // Actually, looking at the code I read earlier, I didn't see a specifically exported getGroup endpoint 
            // other than listGroups. Wait, I see updateGroup gets one.
            // Let's assume listGroups(id) isn't there, so we iterate listGroups. 
            // Optimization: Add getGroup endpoint later. For now, fetch all groups and find.

            const groups = await checklistsApi.listGroups(null, true) // assuming global template
            const found = groups.data.find(g => g.id === parseInt(id))

            if (found) {
                setGroup(found)
                setItems(found.items || [])
            } else {
                toast.error('Template not found')
                navigate('/library')
            }
        } catch (error) {
            console.error(error)
            toast.error('Failed to load data')
        } finally {
            setLoading(false)
        }
    }

    const handleDeleteItem = async (itemId) => {
        if (!confirm("Delete this task?")) return
        try {
            await checklistsApi.deleteItem(itemId)
            toast.success("Task deleted")
            fetchData()
        } catch (e) {
            toast.error("Failed to delete")
        }
    }

    if (loading) return <div className="p-8 text-center text-dark-500">Loading...</div>
    if (!group) return null

    return (
        <div className="max-w-7xl mx-auto h-[calc(100vh-6rem)] flex gap-6">
            {/* Left: Task List */}
            <div className={`flex-1 flex flex-col ${editingItem ? 'hidden md:flex' : ''}`}>
                <div className="flex items-center gap-4 mb-6">
                    <button onClick={() => navigate('/library')} className="btn-ghost p-2">
                        <ArrowLeft size={20} />
                    </button>
                    <div>
                        <h1 className="text-2xl font-bold text-dark-50">{group.name}</h1>
                        <p className="text-dark-400 text-sm">Template Configuration</p>
                    </div>
                    <div className="ml-auto">
                        <button
                            onClick={() => setEditingItem('new')}
                            className="btn-primary"
                        >
                            <Plus size={18} />
                            Add Task
                        </button>
                    </div>
                </div>

                <div className="space-y-3 overflow-y-auto pr-2 pb-20">
                    {items.map((item, index) => (
                        <div
                            key={item.id}
                            className="card p-4 hover:border-dark-600 transition-colors group cursor-pointer"
                            onClick={() => setEditingItem(item)}
                        >
                            <div className="flex items-start justify-between">
                                <div className="flex gap-3">
                                    <span className="text-dark-500 font-mono text-sm">#{index + 1}</span>
                                    <div>
                                        <h3 className="font-semibold text-dark-100">{item.name}</h3>
                                        <code className="text-xs text-accent-primary bg-dark-800 px-1.5 py-0.5 rounded mt-1 block w-fit">
                                            {item.command_template}
                                        </code>
                                    </div>
                                </div>
                                <div className="flex gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                                    <button
                                        onClick={(e) => { e.stopPropagation(); handleDeleteItem(item.id) }}
                                        className="p-1.5 hover:text-accent-danger rounded"
                                    >
                                        <Trash2 size={16} />
                                    </button>
                                </div>
                            </div>
                            {/* Regex badges */}
                            {item.output_regex && Object.keys(item.output_regex).length > 0 && (
                                <div className="mt-3 flex gap-2 flex-wrap">
                                    {Object.keys(item.output_regex).map(key => (
                                        <span key={key} className="px-2 py-0.5 rounded-full bg-dark-800 text-dark-400 text-xs border border-dark-700">
                                            {key}
                                        </span>
                                    ))}
                                </div>
                            )}
                        </div>
                    ))}
                    {items.length === 0 && (
                        <div className="text-center py-12 text-dark-500 border-dashed border-2 border-dark-700 rounded-xl">
                            No tasks defined yet.
                        </div>
                    )}
                </div>
            </div>

            {/* Right: Editor Panel */}
            {editingItem && (
                <div className="w-full md:w-[480px] lg:w-[600px] border-l border-dark-700 pl-6 bg-dark-900/50">
                    <ChecklistItemForm
                        group_id={group.id}
                        item={editingItem === 'new' ? null : editingItem}
                        onSuccess={() => {
                            setEditingItem(null)
                            fetchData()
                        }}
                        onCancel={() => setEditingItem(null)}
                    />
                </div>
            )}
        </div>
    )
}
