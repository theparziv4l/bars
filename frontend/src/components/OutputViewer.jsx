import { useState, useEffect, useRef, useMemo, useCallback, memo } from 'react'
import { Search, ChevronUp, ChevronDown, X, Copy, Check, History, Trash2, CheckCircle2, XCircle, Clock, Loader2, Filter, Highlighter, Plus, Ban, Maximize2, Minimize2, AArrowUp, AArrowDown } from 'lucide-react'
import toast from 'react-hot-toast'

function ExecutionHistoryDropdown({ executionHistory, selectedExecutionId, onSelectExecution, onDeleteExecution }) {
    const [open, setOpen] = useState(false)
    const dropdownRef = useRef(null)

    useEffect(() => {
        const handleClickOutside = (e) => {
            if (dropdownRef.current && !dropdownRef.current.contains(e.target)) {
                setOpen(false)
            }
        }
        if (open) document.addEventListener('mousedown', handleClickOutside)
        return () => document.removeEventListener('mousedown', handleClickOutside)
    }, [open])

    if (!executionHistory || executionHistory.length === 0) return null

    const selected = executionHistory.find(e => e.id === selectedExecutionId)
    const statusIcon = (status) => {
        switch (status) {
            case 'completed': return <CheckCircle2 size={12} className="text-accent-success" />
            case 'failed': return <XCircle size={12} className="text-accent-danger" />
            case 'running': return <Loader2 size={12} className="text-accent-info animate-spin" />
            case 'timeout': return <Clock size={12} className="text-accent-warning" />
            default: return <Clock size={12} className="text-dark-400" />
        }
    }

    const formatDate = (dateStr) => {
        if (!dateStr) return 'Pending'
        const d = new Date(dateStr)
        return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) + ' ' + d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
    }

    return (
        <div className="relative" ref={dropdownRef}>
            <button
                onClick={() => setOpen(!open)}
                className="flex items-center gap-1.5 px-2 py-1.5 text-xs bg-dark-900 border border-dark-700 rounded hover:border-dark-500 transition-colors text-dark-200"
                title="Execution history"
            >
                <History size={13} />
                <span className="max-w-[160px] truncate">
                    Run #{selected?.version || '?'}
                    {selected?.started_at ? ` - ${formatDate(selected.started_at)}` : ''}
                </span>
                <ChevronDown size={12} className={`transition-transform ${open ? 'rotate-180' : ''}`} />
            </button>

            {open && (
                <div className="absolute top-full left-0 mt-1 w-80 bg-dark-900 border border-dark-700 rounded-lg shadow-xl z-50 max-h-64 overflow-y-auto overflow-x-hidden">
                    {executionHistory.map((exec) => (
                        <div
                            key={exec.id}
                            className={`grid grid-cols-[28px_1fr] items-center px-3 py-2 text-xs transition-colors
                                ${exec.id === selectedExecutionId ? 'bg-accent-primary/15 text-dark-100' : 'text-dark-300 hover:bg-dark-800'}
                            `}
                        >
                            {exec.status !== 'running' && exec.status !== 'pending' ? (
                                <button
                                    onClick={(e) => {
                                        e.stopPropagation()
                                        if (onDeleteExecution) onDeleteExecution(exec.id)
                                    }}
                                    className="p-1.5 text-dark-400 hover:text-accent-danger hover:bg-dark-700 rounded transition-colors justify-self-center"
                                    title="Delete this execution"
                                >
                                    <Trash2 size={14} />
                                </button>
                            ) : <span />}
                            <button
                                className="flex items-center gap-2 min-w-0 text-left"
                                onClick={() => {
                                    onSelectExecution(exec.id)
                                    setOpen(false)
                                }}
                            >
                                {statusIcon(exec.status)}
                                <span className="font-medium whitespace-nowrap">Run #{exec.version}</span>
                                <span className="text-dark-500 truncate">{formatDate(exec.started_at)}</span>
                            </button>
                        </div>
                    ))}
                </div>
            )}
        </div>
    )
}

async function copyToClipboard(text) {
    if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text)
        return
    }
    const textarea = document.createElement('textarea')
    textarea.value = text
    textarea.style.position = 'fixed'
    textarea.style.left = '-9999px'
    textarea.style.opacity = '0'
    document.body.appendChild(textarea)
    textarea.select()
    try {
        document.execCommand('copy')
    } finally {
        document.body.removeChild(textarea)
    }
}

function escapeRegex(str) {
    return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function highlightTerms(text, terms) {
    if (!terms.length) return [text]
    const pattern = terms.map(t => `(${escapeRegex(t)})`).join('|')
    const regex = new RegExp(pattern, 'gi')
    return text.split(regex).filter(p => p !== undefined)
}

function isHighlightedPart(part, terms) {
    return terms.some(t => part.toLowerCase() === t.toLowerCase())
}

const OutputViewer = memo(function OutputViewer({
    content,
    command,
    className = '',
    executionHistory,
    selectedExecutionId,
    onSelectExecution,
    onDeleteExecution,
    isFullscreen,
    onToggleFullscreen,
}) {
    const [searchTerm, setSearchTerm] = useState('')
    const [currentMatchIndex, setCurrentMatchIndex] = useState(0)
    const [copied, setCopied] = useState(false)
    const [commandCopied, setCommandCopied] = useState(false)
    const [fontSize, setFontSize] = useState(12)
    const containerRef = useRef(null)
    const matchRefs = useRef([])

    const [filterMode, setFilterMode] = useState(false)
    const [filters, setFilters] = useState([])
    const [filterLogic, setFilterLogic] = useState('and')
    const [filterInput, setFilterInput] = useState('')

    useEffect(() => {
        setCurrentMatchIndex(0)
    }, [searchTerm])

    const addFilter = useCallback((term) => {
        if (!term.trim()) return
        setFilters(prev => {
            if (prev.some(f => f.term.toLowerCase() === term.trim().toLowerCase())) return prev
            return [...prev, { term: term.trim(), inverted: false }]
        })
        setFilterInput('')
    }, [])

    const removeFilter = useCallback((idx) => {
        setFilters(prev => prev.filter((_, i) => i !== idx))
    }, [])

    const toggleFilterInverted = useCallback((idx) => {
        setFilters(prev => prev.map((f, i) => i === idx ? { ...f, inverted: !f.inverted } : f))
    }, [])

    const { filteredLines, totalLines, activeFilterTerms } = useMemo(() => {
        if (!content) return { filteredLines: [], totalLines: 0, activeFilterTerms: [] }
        const lines = content.split('\n')
        const totalLines = lines.length

        if (!filterMode || filters.length === 0) {
            return { filteredLines: null, totalLines, activeFilterTerms: [] }
        }

        const includeFilters = filters.filter(f => !f.inverted)
        const excludeFilters = filters.filter(f => f.inverted)
        const activeTerms = includeFilters.map(f => f.term)

        const result = []
        for (let i = 0; i < lines.length; i++) {
            const line = lines[i]
            const excludeMatch = excludeFilters.some(f =>
                line.toLowerCase().includes(f.term.toLowerCase())
            )
            if (excludeMatch) continue

            if (includeFilters.length === 0) {
                result.push({ lineNum: i + 1, text: line })
                continue
            }

            if (filterLogic === 'and') {
                const allMatch = includeFilters.every(f =>
                    line.toLowerCase().includes(f.term.toLowerCase())
                )
                if (allMatch) result.push({ lineNum: i + 1, text: line })
            } else {
                const anyMatch = includeFilters.some(f =>
                    line.toLowerCase().includes(f.term.toLowerCase())
                )
                if (anyMatch) result.push({ lineNum: i + 1, text: line })
            }
        }

        return { filteredLines: result, totalLines, activeFilterTerms: activeTerms }
    }, [content, filters, filterLogic, filterMode])

    const { parts, matchCount } = useMemo(() => {
        if (!content) return { parts: [], matchCount: 0 }
        if (filterMode) return { parts: [], matchCount: 0 }
        if (!searchTerm) return { parts: [content], matchCount: 0 }

        const regex = new RegExp(`(${escapeRegex(searchTerm)})`, 'gi')
        const parts = content.split(regex)
        const matchCount = parts.length > 1 ? Math.floor(parts.length / 2) : 0

        return { parts, matchCount }
    }, [content, searchTerm, filterMode])

    useEffect(() => {
        if (matchCount > 0 && matchRefs.current[currentMatchIndex]) {
            matchRefs.current[currentMatchIndex].scrollIntoView({
                behavior: 'smooth',
                block: 'center',
            })
        }
    }, [currentMatchIndex, matchCount, searchTerm])

    useEffect(() => {
        matchRefs.current = matchRefs.current.slice(0, matchCount)
    }, [matchCount])

    const handleNext = useCallback(() => {
        if (matchCount === 0) return
        setCurrentMatchIndex((prev) => (prev + 1) % matchCount)
    }, [matchCount])

    const handlePrev = useCallback(() => {
        if (matchCount === 0) return
        setCurrentMatchIndex((prev) => (prev - 1 + matchCount) % matchCount)
    }, [matchCount])

    const getVisibleText = useCallback(() => {
        if (filterMode && filteredLines) {
            return filteredLines.map(l => l.text).join('\n')
        }
        return content || ''
    }, [content, filterMode, filteredLines])

    const handleCopy = useCallback(async () => {
        const text = getVisibleText()
        if (!text) return
        try {
            await copyToClipboard(text)
            setCopied(true)
            toast.success('Output copied')
            setTimeout(() => setCopied(false), 2000)
        } catch {
            toast.error('Failed to copy to clipboard')
        }
    }, [getVisibleText])

    const handleCopyCommand = useCallback(async () => {
        if (!command) return
        try {
            await copyToClipboard(command)
            setCommandCopied(true)
            toast.success('Command copied')
            setTimeout(() => setCommandCopied(false), 2000)
        } catch {
            toast.error('Failed to copy command')
        }
    }, [command])

    const handleSearchChange = useCallback((e) => {
        setSearchTerm(e.target.value)
    }, [])

    const handleClearSearch = useCallback(() => {
        setSearchTerm('')
    }, [])

    const handleFilterKeyDown = useCallback((e) => {
        if (e.key === 'Enter') {
            e.preventDefault()
            addFilter(filterInput)
        }
    }, [filterInput, addFilter])

    const maxLineNumWidth = useMemo(() => {
        if (!filteredLines) return 3
        const max = filteredLines.reduce((m, l) => Math.max(m, l.lineNum), 0)
        return String(max).length
    }, [filteredLines])

    const renderFilteredContent = () => {
        if (!filteredLines) return null
        if (filteredLines.length === 0) {
            return <span className="text-dark-500 italic">No lines match filters ({totalLines} lines total)</span>
        }
        return (
            <div className="flex flex-col">
                {filteredLines.map(({ lineNum, text }) => {
                    const highlighted = highlightTerms(text, activeFilterTerms)
                    return (
                        <div key={lineNum} className="flex hover:bg-dark-900/50">
                            <span
                                className="select-none text-dark-600 text-right pr-3 border-r border-dark-800 mr-3 shrink-0"
                                style={{ minWidth: `${maxLineNumWidth + 1}ch` }}
                            >
                                {lineNum}
                            </span>
                            <span className="flex-1 break-all">
                                {highlighted.map((part, i) =>
                                    isHighlightedPart(part, activeFilterTerms)
                                        ? <span key={i} className="bg-accent-warning/40 text-white rounded-sm px-0.5">{part}</span>
                                        : <span key={i}>{part}</span>
                                )}
                            </span>
                        </div>
                    )
                })}
            </div>
        )
    }

    return (
        <div className={`flex flex-col h-full ${className}`}>
            {/* Command bar */}
            {command && (
                <div
                    className="flex items-center gap-2 px-3 py-1.5 bg-dark-900 border-b border-dark-700 font-mono text-xs cursor-pointer group"
                    onClick={handleCopyCommand}
                    title="Click to copy command"
                >
                    <span className="text-accent-success shrink-0 font-bold">$</span>
                    <span className="text-accent-info break-all flex-1 select-all">{command}</span>
                    <span className="shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
                        {commandCopied
                            ? <Check size={13} className="text-accent-success" />
                            : <Copy size={13} className="text-dark-500" />
                        }
                    </span>
                </div>
            )}

            {/* Toolbar */}
            <div className="flex flex-col bg-dark-800 border-b border-dark-700">
                <div className="flex items-center justify-between p-2">
                    <div className="flex items-center gap-2 flex-1">
                        {/* Mode toggle */}
                        <div className="flex bg-dark-900 rounded border border-dark-700 shrink-0">
                            <button
                                onClick={() => setFilterMode(false)}
                                className={`p-1.5 text-xs flex items-center gap-1 transition-colors rounded-l ${!filterMode ? 'bg-accent-primary/20 text-accent-primary' : 'text-dark-400 hover:text-dark-200'}`}
                                title="Highlight mode"
                            >
                                <Highlighter size={13} />
                            </button>
                            <button
                                onClick={() => setFilterMode(true)}
                                className={`p-1.5 text-xs flex items-center gap-1 transition-colors rounded-r border-l border-dark-700 ${filterMode ? 'bg-accent-primary/20 text-accent-primary' : 'text-dark-400 hover:text-dark-200'}`}
                                title="Filter mode (grep)"
                            >
                                <Filter size={13} />
                            </button>
                        </div>

                        {!filterMode ? (
                            <>
                                <div className="relative flex-1 max-w-xs">
                                    <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-dark-400" />
                                    <input
                                        type="text"
                                        placeholder="Search output..."
                                        className="w-full bg-dark-900 border border-dark-700 rounded pl-8 pr-8 py-1.5 text-sm focus:border-accent-primary focus:outline-none text-dark-100 placeholder-dark-500"
                                        value={searchTerm}
                                        onChange={handleSearchChange}
                                    />
                                    {searchTerm && (
                                        <button
                                            onClick={handleClearSearch}
                                            className="absolute right-2 top-1/2 -translate-y-1/2 text-dark-500 hover:text-dark-300"
                                        >
                                            <X size={12} />
                                        </button>
                                    )}
                                </div>

                                {searchTerm && matchCount > 0 && (
                                    <div className="flex items-center gap-1 text-sm text-dark-300 whitespace-nowrap">
                                        <span className="w-16 text-center text-xs">
                                            {currentMatchIndex + 1} of {matchCount}
                                        </span>
                                        <div className="flex bg-dark-900 rounded border border-dark-700">
                                            <button
                                                onClick={handlePrev}
                                                className="p-1 hover:bg-dark-700 text-dark-400 hover:text-white border-r border-dark-700"
                                                title="Previous match"
                                            >
                                                <ChevronUp size={14} />
                                            </button>
                                            <button
                                                onClick={handleNext}
                                                className="p-1 hover:bg-dark-700 text-dark-400 hover:text-white"
                                                title="Next match"
                                            >
                                                <ChevronDown size={14} />
                                            </button>
                                        </div>
                                    </div>
                                )}

                                {searchTerm && matchCount === 0 && (
                                    <span className="text-xs text-dark-500 italic">No matches</span>
                                )}
                            </>
                        ) : (
                            <>
                                {/* Filter input */}
                                <div className="relative flex-1 max-w-xs">
                                    <Filter size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-dark-400" />
                                    <input
                                        type="text"
                                        placeholder="Add filter (Enter to add)..."
                                        className="w-full bg-dark-900 border border-dark-700 rounded pl-8 pr-8 py-1.5 text-sm focus:border-accent-primary focus:outline-none text-dark-100 placeholder-dark-500"
                                        value={filterInput}
                                        onChange={(e) => setFilterInput(e.target.value)}
                                        onKeyDown={handleFilterKeyDown}
                                    />
                                    {filterInput && (
                                        <button
                                            onClick={() => addFilter(filterInput)}
                                            className="absolute right-2 top-1/2 -translate-y-1/2 text-dark-400 hover:text-accent-primary"
                                            title="Add filter"
                                        >
                                            <Plus size={14} />
                                        </button>
                                    )}
                                </div>

                                {/* AND/OR toggle */}
                                {filters.length > 1 && (
                                    <button
                                        onClick={() => setFilterLogic(prev => prev === 'and' ? 'or' : 'and')}
                                        className={`px-2 py-1 text-xs font-bold rounded border transition-colors shrink-0 ${
                                            filterLogic === 'and'
                                                ? 'bg-accent-primary/20 text-accent-primary border-accent-primary/30'
                                                : 'bg-accent-warning/20 text-accent-warning border-accent-warning/30'
                                        }`}
                                        title={`Switch to ${filterLogic === 'and' ? 'OR' : 'AND'} logic`}
                                    >
                                        {filterLogic.toUpperCase()}
                                    </button>
                                )}

                                {/* Filter stats */}
                                {filters.length > 0 && filteredLines && (
                                    <span className="text-xs text-dark-400 whitespace-nowrap shrink-0">
                                        {filteredLines.length}/{totalLines} lines
                                    </span>
                                )}
                            </>
                        )}
                    </div>

                    <div className="flex items-center gap-1 shrink-0 ml-2">
                        {executionHistory && executionHistory.length > 0 && (
                            <ExecutionHistoryDropdown
                                executionHistory={executionHistory}
                                selectedExecutionId={selectedExecutionId}
                                onSelectExecution={onSelectExecution}
                                onDeleteExecution={onDeleteExecution}
                            />
                        )}

                        {/* Font size controls */}
                        <div className="flex items-center bg-dark-900 rounded border border-dark-700">
                            <button
                                onClick={() => setFontSize(s => Math.max(8, s - 2))}
                                className="p-1 text-dark-400 hover:text-white hover:bg-dark-700 transition-colors rounded-l"
                                title="Decrease font size"
                            >
                                <AArrowDown size={14} />
                            </button>
                            <span className="text-[10px] text-dark-500 px-1 select-none">{fontSize}</span>
                            <button
                                onClick={() => setFontSize(s => Math.min(24, s + 2))}
                                className="p-1 text-dark-400 hover:text-white hover:bg-dark-700 transition-colors rounded-r"
                                title="Increase font size"
                            >
                                <AArrowUp size={14} />
                            </button>
                        </div>

                        <button
                            onClick={handleCopy}
                            className="p-1.5 text-dark-400 hover:text-white hover:bg-dark-700 rounded transition-colors"
                            title={filterMode && filteredLines ? 'Copy filtered output' : 'Copy output'}
                        >
                            {copied ? <Check size={16} className="text-accent-success" /> : <Copy size={16} />}
                        </button>

                        {onToggleFullscreen && (
                            <button
                                onClick={onToggleFullscreen}
                                className="p-1.5 text-dark-400 hover:text-white hover:bg-dark-700 rounded transition-colors"
                                title={isFullscreen ? 'Exit fullscreen' : 'Fullscreen'}
                            >
                                {isFullscreen ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
                            </button>
                        )}
                    </div>
                </div>

                {/* Filter chips row */}
                {filterMode && filters.length > 0 && (
                    <div className="flex items-center gap-1.5 px-2 pb-2 flex-wrap">
                        {filters.map((f, idx) => (
                            <div
                                key={idx}
                                className={`flex items-center gap-1 px-2 py-0.5 rounded-full text-xs border transition-colors ${
                                    f.inverted
                                        ? 'bg-accent-danger/15 border-accent-danger/30 text-accent-danger'
                                        : 'bg-accent-primary/15 border-accent-primary/30 text-accent-primary'
                                }`}
                            >
                                <button
                                    onClick={() => toggleFilterInverted(idx)}
                                    className={`hover:opacity-80 transition-opacity ${f.inverted ? 'line-through' : ''}`}
                                    title={f.inverted ? 'Click to include' : 'Click to exclude (NOT)'}
                                >
                                    {f.inverted && <Ban size={10} className="inline mr-0.5" />}
                                    {f.term}
                                </button>
                                <button
                                    onClick={() => removeFilter(idx)}
                                    className="hover:text-white transition-colors ml-0.5"
                                    title="Remove filter"
                                >
                                    <X size={11} />
                                </button>
                            </div>
                        ))}
                        <button
                            onClick={() => setFilters([])}
                            className="text-xs text-dark-500 hover:text-dark-300 transition-colors px-1"
                            title="Clear all filters"
                        >
                            Clear
                        </button>
                    </div>
                )}
            </div>

            {/* Content */}
            <div
                ref={containerRef}
                className="flex-1 overflow-auto p-4 bg-dark-950 font-mono text-dark-200 whitespace-pre-wrap font-ligatures-none"
                style={{ fontSize: `${fontSize}px` }}
            >
                {filterMode && filters.length > 0 ? (
                    renderFilteredContent()
                ) : searchTerm && !filterMode ? (
                    <>
                        {parts.map((part, i) => {
                            if (i % 2 === 0) {
                                return <span key={i}>{part}</span>
                            } else {
                                const matchIndex = Math.floor(i / 2)
                                const isCurrent = matchIndex === currentMatchIndex
                                return (
                                    <span
                                        key={i}
                                        ref={el => matchRefs.current[matchIndex] = el}
                                        className={`${isCurrent ? 'bg-accent-warning text-dark-950 font-bold' : 'bg-accent-warning/40 text-white'} rounded-sm px-0.5 transition-colors duration-200`}
                                    >
                                        {part}
                                    </span>
                                )
                            }
                        })}
                    </>
                ) : (
                    content || <span className="text-dark-500 italic">No output</span>
                )}
            </div>
        </div>
    )
})

export default OutputViewer
