<template>
  <div class="xlsx-preview">
    <div v-if="loading" class="xlsx-preview-state">
      <a-spin />
      <span>正在解析 Excel 文件…</span>
    </div>

    <div v-else-if="error" class="xlsx-preview-state xlsx-preview-error">
      <AlertCircle :size="20" />
      <div class="xlsx-preview-error-text">
        <div>{{ error }}</div>
        <a v-if="downloadUrl" :href="downloadUrl" :download="filename" class="xlsx-preview-download-link">
          下载原始文件
        </a>
      </div>
    </div>

    <template v-else-if="sheets.length">
      <div v-if="sheets.length > 1 || showToolbar" class="xlsx-preview-toolbar">
        <div v-if="sheets.length > 1" class="xlsx-sheet-tabs" role="tablist">
          <button
            v-for="sheet in sheets"
            :key="sheet.name"
            type="button"
            role="tab"
            :aria-selected="sheet.name === activeSheetName"
            :class="['xlsx-sheet-tab', { active: sheet.name === activeSheetName }]"
            @click="activeSheetName = sheet.name"
          >
            <span class="xlsx-sheet-tab-name">{{ sheet.name }}</span>
            <span v-if="sheet.rowCount > 0" class="xlsx-sheet-tab-count">{{ sheet.rowCount }}</span>
          </button>
        </div>
        <div v-if="sheets.length > 1 || activeSheet.rowCount > 0" class="xlsx-toolbar-actions">
          <a-input
            v-model:value="searchTerm"
            placeholder="搜索单元格内容"
            allow-clear
            class="xlsx-search-input"
          >
            <template #prefix>
              <Search :size="14" />
            </template>
          </a-input>
          <span v-if="activeSheet.rowCount > 0" class="xlsx-row-info">
            {{ filteredRowCount }} / {{ activeSheet.rowCount }} 行
            <span v-if="rowLimitReached" class="xlsx-row-info-warning">
              · 仅显示前 {{ MAX_RENDERED_ROWS }} 行，<a :href="downloadUrl" :download="filename">下载完整文件</a>
            </span>
          </span>
        </div>
      </div>

      <div class="xlsx-sheet-scroll">
        <table v-if="renderedRows.length" class="xlsx-sheet-table">
          <colgroup>
            <col v-if="hasMerges" class="xlsx-col-rownum" />
            <col
              v-for="(width, idx) in activeSheet.columnWidths"
              :key="idx"
              :style="width ? { width: `${width}px` } : undefined"
            />
          </colgroup>
          <thead v-if="activeSheet.headerRow">
            <tr>
              <th v-if="hasMerges" class="xlsx-cell-rownum"></th>
              <th
                v-for="(cell, idx) in activeSheet.headerRow"
                :key="`h-${idx}`"
                class="xlsx-cell-header"
              >
                <span v-if="cell.value !== null && cell.value !== undefined && String(cell.value) !== ''">
                  {{ cell.value }}
                </span>
              </th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="(row, rowIdx) in renderedRows"
              :key="`r-${row.rowIndex}`"
              :class="{ 'xlsx-row-alt': rowIdx % 2 === 1 }"
            >
              <td v-if="hasMerges" class="xlsx-cell-rownum">{{ row.rowIndex + 1 }}</td>
              <td
                v-for="(cell, colIdx) in row.cells"
                :key="`c-${row.rowIndex}-${colIdx}`"
                class="xlsx-cell"
                :class="cellClass(cell)"
                :rowspan="cell.rowspan > 1 ? cell.rowspan : undefined"
                :colspan="cell.colspan > 1 ? cell.colspan : undefined"
                :title="cell.title || undefined"
              >
                <span v-if="cell.isMergedHidden"></span>
                <span v-else-if="cell.value === null || cell.value === undefined"></span>
                <span v-else class="xlsx-cell-value" :class="{ 'xlsx-cell-search-hit': isSearchHit(cell.value) }">
                  {{ cell.value }}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
        <div v-else class="xlsx-empty-sheet">此工作表没有数据</div>
      </div>
    </template>

    <div v-else class="xlsx-preview-state">
      <FileSpreadsheet :size="20" />
      <span>未发现可显示的工作表</span>
    </div>
  </div>
</template>

<script setup>
import { computed, onUnmounted, ref, watch } from 'vue'
import * as XLSX from 'xlsx'
import { AlertCircle, FileSpreadsheet, Search } from 'lucide-vue-next'

const props = defineProps({
  url: { type: String, required: true },
  filename: { type: String, default: '' },
  downloadUrl: { type: String, default: '' }
})

const MAX_RENDERED_ROWS = 5000

const loading = ref(false)
const error = ref('')
const sheets = ref([])
const activeSheetName = ref('')
const searchTerm = ref('')

let fetchedBlobUrl = null

const activeSheet = computed(() => {
  if (!sheets.value.length) return { name: '', rowCount: 0, headerRow: null, rows: [], merges: [], columnWidths: [] }
  return sheets.value.find((s) => s.name === activeSheetName.value) || sheets.value[0]
})

const renderedRows = computed(() => {
  const rows = activeSheet.value.rows || []
  if (!searchTerm.value.trim()) {
    return rows.slice(0, MAX_RENDERED_ROWS)
  }
  const needle = searchTerm.value.trim().toLowerCase()
  const matched = rows.filter((row) => row.cells.some((cell) => isSearchHit(cell.value, needle)))
  return matched.slice(0, MAX_RENDERED_ROWS)
})

const rowLimitReached = computed(() => (activeSheet.value.rows || []).length > MAX_RENDERED_ROWS)

const filteredRowCount = computed(() => {
  const rows = activeSheet.value.rows || []
  if (!searchTerm.value.trim()) return rows.length
  const needle = searchTerm.value.trim().toLowerCase()
  return rows.filter((row) => row.cells.some((cell) => isSearchHit(cell.value, needle))).length
})

const hasMerges = computed(() => (activeSheet.value.merges || []).length > 0)

onUnmounted(() => {
  if (fetchedBlobUrl) URL.revokeObjectURL(fetchedBlobUrl)
})

watch(
  () => props.url,
  (next) => {
    if (next) loadWorkbook(next)
  },
  { immediate: true }
)

async function loadWorkbook(url) {
  loading.value = true
  error.value = ''
  sheets.value = []
  activeSheetName.value = ''
  if (fetchedBlobUrl) {
    URL.revokeObjectURL(fetchedBlobUrl)
    fetchedBlobUrl = null
  }
  try {
    const response = await fetch(url)
    if (!response.ok) {
      throw new Error(`下载失败 (${response.status})`)
    }
    const blob = await response.blob()
    fetchedBlobUrl = URL.createObjectURL(blob)
    const buffer = await blob.arrayBuffer()
    const workbook = XLSX.read(buffer, { type: 'array', cellDates: false })
    const parsed = workbook.SheetNames.map((name) => parseSheet(name, workbook.Sheets[name]))
    if (!parsed.length) {
      throw new Error('未发现任何工作表')
    }
    sheets.value = parsed
    activeSheetName.value = parsed[0].name
  } catch (err) {
    error.value = err?.message || '解析失败'
  } finally {
    loading.value = false
  }
}

function parseSheet(name, sheet) {
  const range = sheet['!ref'] ? XLSX.utils.decode_range(sheet['!ref']) : null
  if (!range || range.e < range.s.r || range.e < range.s.c) {
    return { name: name || 'Sheet', rowCount: 0, headerRow: null, rows: [], merges: [], columnWidths: [] }
  }
  const merges = sheet['!merges'] || []
  const mergeMap = buildMergeMap(merges)
  const colWidths = extractColumnWidths(sheet, range)
  const rows = []
  for (let r = range.s.r; r <= range.e.r; r += 1) {
    const cells = []
    for (let c = range.s.c; c <= range.e.c; c += 1) {
      const address = XLSX.utils.encode_cell({ r, c })
      const cellInfo = buildCell(sheet[address], address, r, c, mergeMap)
      cells.push(cellInfo)
    }
    rows.push({ rowIndex: r, cells })
  }
  const firstRow = rows[0]
  const headerRow = firstRow && firstRow.cells.some((c) => c.value !== null && c.value !== undefined && c.value !== '')
    ? firstRow.cells
    : null
  const dataRows = headerRow ? rows.slice(1) : rows
  return {
    name: name || 'Sheet',
    rowCount: dataRows.length,
    headerRow,
    rows: dataRows,
    merges,
    columnWidths: colWidths
  }
}

function buildCell(rawCell, address, row, col, mergeMap) {
  const mergeInfo = mergeMap.get(address)
  if (mergeInfo && (mergeInfo.hidden || (mergeInfo.master && mergeInfo.rowspan <= 0))) {
    return { value: null, rowspan: 0, colspan: 0, isMergedHidden: true }
  }
  let value = null
  if (rawCell) {
    if (rawCell.w !== undefined) value = rawCell.w
    else if (rawCell.v !== undefined && rawCell.v !== null) value = String(rawCell.v)
  }
  return {
    value,
    rowspan: mergeInfo?.rowspan ?? 1,
    colspan: mergeInfo?.colspan ?? 1,
    isMergedHidden: false,
    title: value == null ? '' : String(value)
  }
}

function buildMergeMap(merges) {
  const map = new Map()
  for (const merge of merges) {
    const { s, e } = merge
    const rowspan = e.r - s.r + 1
    const colspan = e.c - s.c + 1
    const masterAddress = XLSX.utils.encode_cell(s)
    map.set(masterAddress, { master: true, rowspan, colspan })
    for (let r = s.r; r <= e.r; r += 1) {
      for (let c = s.c; c <= e.c; c += 1) {
        if (r === s.r && c === s.c) continue
        const addr = XLSX.utils.encode_cell({ r, c })
        map.set(addr, { hidden: true, rowspan: 0, colspan: 0 })
      }
    }
  }
  return map
}

function extractColumnWidths(sheet, range) {
  const cols = sheet['!cols'] || []
  const widths = []
  for (let c = range.s.c; c <= range.e.c; c += 1) {
    const col = cols[c]
    const width = col?.wpx || (col?.w ? col.w * 7 : null)
    if (width) widths.push(Math.round(width))
  }
  return widths
}

function cellClass(cell) {
  return {
    'xlsx-cell-empty': cell.value === null || cell.value === undefined || cell.value === '',
    'xlsx-cell-merged': cell.rowspan > 1 || cell.colspan > 1
  }
}

function isSearchHit(value, needle) {
  if (value == null) return false
  const text = String(value)
  const target = needle ?? searchTerm.value.trim().toLowerCase()
  if (!target) return false
  return text.toLowerCase().includes(target)
}
</script>

<style scoped lang="less">
.xlsx-preview {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  min-height: 0;
  background: var(--gray-0, #fafafa);
  color: var(--gray-12, #1f1f1f);
}

.xlsx-preview-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 48px 16px;
  color: var(--gray-11, #595959);
  font-size: 13px;
}

.xlsx-preview-error {
  color: var(--red-11, #d4380d);
}

.xlsx-preview-error-text {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  text-align: center;
}

.xlsx-preview-download-link {
  color: var(--brand-9, #1677ff);
  text-decoration: none;
  font-size: 13px;

  &:hover {
    text-decoration: underline;
  }
}

.xlsx-preview-toolbar {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--gray-3, #e5e7eb);
  background: var(--gray-1, #fff);
}

.xlsx-sheet-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  max-width: 100%;
  overflow-x: auto;
  scrollbar-width: thin;
}

.xlsx-sheet-tab {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 12px;
  border: 1px solid var(--gray-3, #e5e7eb);
  border-radius: 4px;
  background: var(--gray-1, #fff);
  color: var(--gray-11, #595959);
  font-size: 12px;
  cursor: pointer;
  transition: all 120ms ease;
  white-space: nowrap;

  &:hover {
    border-color: var(--brand-7, #4096ff);
    color: var(--brand-9, #1677ff);
  }

  &.active {
    background: var(--brand-1, #e6f4ff);
    border-color: var(--brand-9, #1677ff);
    color: var(--brand-9, #1677ff);
    font-weight: 500;
  }
}

.xlsx-sheet-tab-count {
  font-size: 11px;
  opacity: 0.7;
  margin-left: 4px;
  padding: 1px 5px;
  border-radius: 8px;
  background: rgba(0, 0, 0, 0.06);
}

.xlsx-sheet-tab.active .xlsx-sheet-tab-count {
  background: rgba(22, 119, 255, 0.12);
}

.xlsx-toolbar-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.xlsx-search-input {
  width: 240px;
  max-width: 100%;
}

.xlsx-row-info {
  font-size: 12px;
  color: var(--gray-10, #6b7280);
}

.xlsx-row-info-warning {
  color: var(--orange-11, #d46b08);
  margin-left: 4px;

  a {
    color: inherit;
    text-decoration: underline;
  }
}

.xlsx-sheet-scroll {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  background: var(--gray-1, #fff);
}

.xlsx-sheet-table {
  border-collapse: separate;
  border-spacing: 0;
  font-size: 13px;
  min-width: 100%;
  table-layout: auto;
}

.xlsx-col-rownum {
  width: 48px;
  min-width: 48px;
  background: var(--gray-2, #f5f5f5);
}

.xlsx-cell-header,
.xlsx-cell {
  border-right: 1px solid var(--gray-3, #e5e7eb);
  border-bottom: 1px solid var(--gray-3, #e5e7eb);
  padding: 6px 10px;
  vertical-align: top;
  text-align: left;
  white-space: pre-wrap;
  word-break: break-word;
  background: var(--gray-1, #fff);
  min-width: 80px;
  max-width: 480px;
}

.xlsx-cell-header {
  position: sticky;
  top: 0;
  z-index: 1;
  background: var(--gray-2, #f5f5f5);
  font-weight: 600;
  color: var(--gray-12, #1f1f1f);
}

.xlsx-cell-rownum {
  border-right: 1px solid var(--gray-3, #e5e7eb);
  border-bottom: 1px solid var(--gray-3, #e5e7eb);
  padding: 4px 8px;
  text-align: right;
  font-size: 11px;
  color: var(--gray-10, #6b7280);
  background: var(--gray-2, #f5f5f5);
  position: sticky;
  left: 0;
  z-index: 1;
}

.xlsx-row-alt .xlsx-cell,
.xlsx-row-alt .xlsx-cell-rownum {
  background: var(--gray-1, #fafafa);
}

.xlsx-row-alt .xlsx-cell-header {
  background: var(--gray-2, #f5f5f5);
}

.xlsx-cell-empty {
  color: var(--gray-7, #bfbfbf);
  background: var(--gray-1, #fff);
}

.xlsx-cell-merged {
  background: var(--gray-2, #f5f5f5);
  font-weight: 500;
}

.xlsx-cell-value {
  display: block;
}

.xlsx-cell-search-hit {
  background: var(--yellow-2, #fff7d6);
  border-radius: 2px;
  padding: 0 2px;
  margin: 0 -2px;
}

.xlsx-empty-sheet {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 48px 16px;
  color: var(--gray-10, #6b7280);
  font-size: 13px;
}
</style>
