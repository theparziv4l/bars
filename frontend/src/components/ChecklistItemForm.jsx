import { useState, useEffect } from 'react'
import { Plus, Trash2, Play, Save, Code, Settings, FileText, Split } from 'lucide-react'
import { checklistsApi } from '../services/api'
import toast from 'react-hot-toast'

export default function ChecklistItemForm({ group_id, item = null, onSuccess, onCancel }) {
    const [formData, setFormData] = useState({
        name: '',
        command_template: '',
        output_regex: {},
        variables: {},
        input_definitions: {},
        storage_policy: {},
        parameter_schema: {},
        enabled: true
    })

    // Regex testing state
    const [testOutput, setTestOutput] = useState('')
    const [testResult, setTestResult] = useState(null)

    useEffect(() => {
        if (item) {
            setFormData({
                name: item.name,
                command_template: item.command_template,
                output_regex: item.output_regex || {},
                variables: item.variables || {},
                input_definitions: item.input_definitions || {},
                storage_policy: item.storage_policy || {},
                parameter_schema: item.parameter_schema || {},
                enabled: item.enabled
            })
        } else {
            setFormData({
                name: '',
                command_template: '',
                output_regex: {},
                variables: {},
                input_definitions: {},
                storage_policy: {},
                parameter_schema: {},
                enabled: true
            })
        }
    }, [item])

    const handleChange = (e) => {
        setFormData({ ...formData, [e.target.name]: e.target.value })
    }

    // --- Command Parsing & Type Schema ---
    const [detectedVars, setDetectedVars] = useState([])

    useEffect(() => {
        const regex = /{([\w_]+)}/g
        const matches = [...formData.command_template.matchAll(regex)].map(m => m[1])
        const uniqueVars = [...new Set(matches)]
        setDetectedVars(uniqueVars)

        // Cleanup schema: remove vars no longer in template
        // setFormData(prev => {
        //     const newSchema = { ...prev.parameter_schema }
        //     let changed = false
        //     Object.keys(newSchema).forEach(key => {
        //         if (!uniqueVars.includes(key)) {
        //             delete newSchema[key]
        //             changed = true
        //         }
        //     })
        //     return changed ? { ...prev, parameter_schema: newSchema } : prev
        // })
        // Note: Avoiding auto-delete loop for now to prevent flicker, but ideally we should clean up.
    }, [formData.command_template])

    const updateSchema = (key, field, value) => {
        setFormData(prev => ({
            ...prev,
            parameter_schema: {
                ...prev.parameter_schema,
                [key]: {
                    ...(prev.parameter_schema?.[key] || { type: 'string' }),
                    [field]: value
                }
            }
        }))
    }

    // --- Regex Management ---
    const addRegex = () => {
        const key = prompt("Enter variable name (e.g., 'open_ports'):")
        if (!key) return
        setFormData(prev => ({
            ...prev,
            output_regex: { ...prev.output_regex, [key]: '' }
        }))
    }

    const updateRegex = (key, value, type = 'regex') => {
        const storedValue = type === 'command' ? `cmd:${value}` : value
        setFormData(prev => ({
            ...prev,
            output_regex: { ...prev.output_regex, [key]: storedValue }
        }))
    }

    const removeRegex = (key) => {
        const newRegex = { ...formData.output_regex }
        delete newRegex[key]

        // Also remove from storage mapping if present
        const newStorage = { ...formData.storage_policy }
        if (newStorage.variable_mapping && newStorage.variable_mapping[key]) {
            delete newStorage.variable_mapping[key]
        }

        setFormData({
            ...formData,
            output_regex: newRegex,
            storage_policy: newStorage
        })
    }

    const updateStorageMapping = (regexKey, varName) => {
        const newPolicy = {
            ...(formData.storage_policy || {}),
            variable_mapping: {
                ...(formData.storage_policy?.variable_mapping || {}),
                [regexKey]: varName
            }
        }
        if (!varName) {
            delete newPolicy.variable_mapping[regexKey]
        }
        setFormData({ ...formData, storage_policy: newPolicy })
    }

    const testRegex = () => {
        if (!testOutput) return
        const results = {}
        try {
            for (const [key, rawPattern] of Object.entries(formData.output_regex)) {
                if (rawPattern.startsWith('cmd:')) {
                    // Command testing not supported in browser purely
                    results[key] = "[Command testing requires backend execution]"
                    continue
                }
                const regex = new RegExp(rawPattern, 'gim') // Match backend: global, case-insensitive, multiline
                const matches = [...testOutput.matchAll(regex)]
                if (matches.length > 0) {
                    // Extract groups if present, else full match
                    results[key] = matches.map(m => m.length > 1 ? m[1] : m[0])
                }
            }
            setTestResult(results)
        } catch (e) {
            setTestResult({ error: e.message })
        }
    }

    // --- Submission ---
    const handleSubmit = async (e) => {
        e.preventDefault()
        try {
            if (item) {
                // The form was seeded from a copy of the item that may be minutes old and
                // is PUT back whole. Check the server's current version first so another
                // operator's edit (or a synced peer change) isn't silently reverted.
                try {
                    const { data: fresh } = await checklistsApi.getItem(item.id)
                    if (fresh?.updated_at && item.updated_at && fresh.updated_at !== item.updated_at) {
                        const proceed = confirm(
                            'This item was changed by someone else while you were editing.\n\nOverwrite their version with yours?'
                        )
                        if (!proceed) return
                    }
                } catch { /* if the check fails, fall through to the normal save */ }

                await checklistsApi.updateItem(item.id, formData)
                toast.success('Item updated')
            } else {
                await checklistsApi.createItem({ ...formData, group_id })
                toast.success('Item created')
            }
            onSuccess()
        } catch (error) {
            toast.error('Failed to save item')
            console.error(error)
        }
    }

    return (
        <form onSubmit={handleSubmit} className="space-y-6">
            <h2 className="text-xl font-semibold text-dark-50">
                {item ? 'Edit Task' : 'New Task'}
            </h2>

            {/* Basic Info */}
            <div className="space-y-4">
                <div>
                    <label className="block text-sm font-medium text-dark-300 mb-1">Task Name</label>
                    <input
                        type="text"
                        name="name"
                        value={formData.name}
                        onChange={handleChange}
                        className="input-field w-full bg-dark-900 text-dark-100 placeholder-dark-500 border-dark-700"
                        required
                    />
                </div>
                <div>
                    <label className="block text-sm font-medium text-dark-300 mb-1">
                        Command Template <span className="text-dark-500">(Use {'{var}'} for variables)</span>
                    </label>
                    <div className="relative font-mono">
                        <textarea
                            name="command_template"
                            value={formData.command_template}
                            onChange={handleChange}
                            rows={3}
                            className="input-field w-full bg-dark-900 text-dark-100 placeholder-dark-500 border-dark-700"
                            placeholder="nmap -p {ports} {target}"
                            required
                        />
                    </div>
                </div>
            </div>

            {/* Command Parameters Schema */}
            {detectedVars.length > 0 && (
                <div className="p-4 bg-dark-800/50 rounded-xl border border-dark-700 space-y-4">
                    <h3 className="font-medium text-dark-200 flex items-center gap-2">
                        <Settings size={16} />
                        Variable Configuration
                    </h3>
                    <p className="text-xs text-dark-400">
                        Configure how variables are passed to the shell.
                    </p>

                    <div className="space-y-3">
                        {detectedVars.map(key => {
                            const config = formData.parameter_schema?.[key] || { type: 'string' }
                            const type = config.type || 'string'

                            return (
                                <div key={key} className="flex flex-col sm:flex-row sm:items-center gap-3 p-3 bg-dark-900/50 rounded-lg border border-dark-800">
                                    <div className="w-32 font-mono text-sm text-accent-info shrink-0">
                                        {key}
                                    </div>

                                    {/* Type Selector */}
                                    <div className="flex items-center gap-2">
                                        <select
                                            className="select-sm bg-dark-800 border-dark-700 rounded text-xs px-2 py-1"
                                            value={type}
                                            onChange={e => updateSchema(key, 'type', e.target.value)}
                                        >
                                            <option value="string">String (Raw)</option>
                                            <option value="file">File (Path)</option>
                                        </select>
                                    </div>

                                    {/* Type Specific Config */}
                                    {type === 'string' && (
                                        <div className="flex items-center gap-2 flex-1">
                                            <span className="text-xs text-dark-500">Delimiter:</span>
                                            <select
                                                className="select-sm bg-dark-800 border-dark-700 rounded text-xs px-2 py-1"
                                                value={config.delimiter || ' '}
                                                onChange={e => updateSchema(key, 'delimiter', e.target.value)}
                                            >
                                                <option value=" ">Space</option>
                                                <option value=",">Comma</option>
                                                <option value="newline">New Line</option>
                                                <option value="none">None</option>
                                            </select>
                                        </div>
                                    )}

                                    {type === 'file' && (
                                        <div className="flex items-center gap-2 flex-1">
                                            <span className="text-xs text-dark-500">Ext:</span>
                                            <input
                                                type="text"
                                                className="input-sm bg-dark-800 border-dark-700 rounded text-xs px-2 py-1 w-16"
                                                placeholder=".txt"
                                                value={config.extension || ''}
                                                onChange={e => updateSchema(key, 'extension', e.target.value)}
                                            />
                                        </div>
                                    )}
                                </div>
                            )
                        })}
                    </div>
                </div>
            )}

            {/* Parsing Engine */}
            <div className="p-4 bg-dark-800/50 rounded-xl border border-dark-700 space-y-4">
                <div className="flex items-center justify-between">
                    <h3 className="font-medium text-dark-200 flex items-center gap-2">
                        <Code size={16} />
                        Output Extraction
                    </h3>
                    <button type="button" onClick={addRegex} className="text-sm btn-ghost">
                        <Plus size={14} /> Add Pattern
                    </button>
                </div>

                <div className="space-y-3">
                    {Object.entries(formData.output_regex).map(([key, rawPattern]) => {
                        const isCommand = rawPattern.startsWith('cmd:')
                        const value = isCommand ? rawPattern.substring(4) : rawPattern

                        return (
                            <div key={key} className="flex items-center gap-2">
                                <div className="flex flex-col gap-1">
                                    <span className="w-32 text-sm font-mono text-accent-primary truncate" title={key}>
                                        {key}
                                    </span>
                                    <select
                                        className="text-[10px] bg-dark-900 border border-dark-700 rounded text-dark-300 p-0.5"
                                        value={isCommand ? 'command' : 'regex'}
                                        onChange={(e) => updateRegex(key, value, e.target.value)}
                                    >
                                        <option value="regex">Regex</option>
                                        <option value="command">Command</option>
                                    </select>
                                </div>
                                <input
                                    type="text"
                                    value={value}
                                    onChange={(e) => updateRegex(key, e.target.value, isCommand ? 'command' : 'regex')}
                                    className="input-field flex-1 font-mono text-xs bg-dark-900 text-dark-100 placeholder-dark-500 border-dark-700"
                                    placeholder={isCommand ? "grep ... | cut ..." : "Regex pattern..."}
                                />
                                <button
                                    type="button"
                                    onClick={() => removeRegex(key)}
                                    className="text-dark-500 hover:text-accent-danger"
                                >
                                    <Trash2 size={16} />
                                </button>
                            </div>
                        )
                    })}
                    {Object.keys(formData.output_regex).length === 0 && (
                        <p className="text-sm text-dark-500 italic">No patterns defined.</p>
                    )}
                </div>

                {/* Tester */}
                <div className="mt-4 pt-4 border-t border-dark-700">
                    <label className="block text-xs font-medium text-dark-400 mb-2">Regex Tester</label>
                    <div className="grid grid-cols-2 gap-4">
                        <textarea
                            value={testOutput}
                            onChange={(e) => setTestOutput(e.target.value)}
                            className="input-field text-xs font-mono h-24 bg-dark-900 text-dark-100 placeholder-dark-500 border-dark-700"
                            placeholder="Paste sample output here..."
                        />
                        <div className="bg-dark-900 rounded-lg p-3 text-xs font-mono overflow-auto h-24 relative">
                            {testResult ? (
                                <pre>{JSON.stringify(testResult, null, 2)}</pre>
                            ) : (
                                <span className="text-dark-600">Results will appear here...</span>
                            )}
                            <button
                                type="button"
                                onClick={testRegex}
                                className="absolute bottom-2 right-2 p-1.5 bg-accent-primary text-white rounded hover:bg-accent-primary/90"
                                title="Run Test"
                            >
                                <Play size={12} />
                            </button>
                        </div>
                    </div>
                </div>
            </div>

            {/* Storage Policy */}
            <div className="p-4 bg-dark-800/50 rounded-xl border border-dark-700 space-y-4">
                <h3 className="font-medium text-dark-200 flex items-center gap-2">
                    <Save size={16} />
                    Variable Storage
                </h3>
                <p className="text-xs text-dark-400">
                    Map extracted outputs to Project Variables.
                </p>
                <div className="space-y-3">
                    {Object.keys(formData.output_regex).length === 0 && (
                        <p className="text-sm text-dark-500 italic">Define output patterns first.</p>
                    )}
                    {Object.keys(formData.output_regex).map(key => (
                        <div key={key} className="flex items-center gap-4">
                            <span className="w-1/3 text-sm font-mono text-dark-300 truncate" title={key}>
                                {key}
                            </span>
                            <span className="text-dark-500">→</span>
                            <input
                                type="text"
                                className="input-field flex-1 text-sm bg-dark-900 border-dark-700"
                                placeholder={`Defaults to ${key}`}
                                value={formData.storage_policy?.variable_mapping?.[key] || ''}
                                onChange={e => updateStorageMapping(key, e.target.value)}
                            />
                        </div>
                    ))}
                </div>
            </div>

            <div className="flex justify-end gap-3 pt-4 border-t border-dark-700">
                <button type="button" onClick={onCancel} className="btn-ghost">Cancel</button>
                <button type="submit" className="btn-primary">
                    <Save size={18} />
                    Save Task
                </button>
            </div>
        </form>
    )
}
