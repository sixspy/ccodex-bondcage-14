export const stubsViewTemplate = `
<section class="panel">
  <h2>增补 / 拆笼</h2>
  <div class="stub-banner">
    子笼扩额纪要（{{ topupMinutes.locked_date || '—' }} 锁死 · {{ topupMinutes.minute_no || '—' }}）：
    仅走 <b>{{ topupMinutes.chosen_path || '甲路径-子笼独立扩代整笔' }}</b>；
    禁用 {{ topupMinutes.rejected_path || '乙路径-父笼桥接' }}。
    偏离即记「子笼扩额偏航」并拒写。
  </div>
  <div class="grid-2">
    <div>
      <h3>子笼独立扩额（增补）</h3>
      <p class="hint">仅对拆笼所得子笼生效：同事务从账户可用余额划入、抬额升代；父笼（含已关闭）零改。</p>
      <div class="form-row">
        <div>
          <label>目标子笼</label>
          <select v-model.number="topupForm.cage_id">
            <option :value="0">请选择</option>
            <option v-for="c in childCages" :key="'t'+c.id" :value="c.id">
              {{ c.cage_code }} · 可用 {{ fmt(c.available) }} · 代{{ c.generation }}
            </option>
          </select>
        </div>
        <div>
          <label>扩额金额</label>
          <input type="number" v-model.number="topupForm.amount" min="1"/>
        </div>
        <div>
          <label>口径（锁死）</label>
          <select v-model="topupForm.mode" disabled>
            <option>子笼独立扩代整笔</option>
          </select>
        </div>
      </div>
      <div class="actions">
        <button @click="doChildTopup">子笼独立扩额</button>
      </div>
      <table>
        <thead><tr><th>ID</th><th>笼</th><th>金额</th><th>模式</th><th>状态</th><th>说明</th></tr></thead>
        <tbody>
          <tr v-for="j in topupJobs" :key="j.id">
            <td>{{ j.id }}</td>
            <td>{{ j.cage_code }}</td>
            <td>{{ fmt(j.amount) }}</td>
            <td>{{ j.mode }}</td>
            <td><span class="badge pass">{{ j.status }}</span></td>
            <td>{{ j.detail }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div>
      <h3>拆笼分拨（整笔切分）</h3>
      <p class="hint">同事务父笼关闭（已拆笼）、生成两只独立子笼；账户冻结总额平移不变。影子子笼模式已禁用。</p>
      <div class="form-row">
        <div>
          <label>待拆父笼</label>
          <select v-model.number="splitForm.cage_id" @change="onSplitCageChange">
            <option :value="0">请选择</option>
            <option v-for="c in splittableCages" :key="'s'+c.id" :value="c.id">
              {{ c.cage_code }} · 可用 {{ fmt(c.available) }}
            </option>
          </select>
        </div>
        <div>
          <label>左金额</label>
          <input type="number" v-model.number="splitForm.left_amount" min="1"/>
        </div>
        <div>
          <label>右金额</label>
          <input type="number" v-model.number="splitForm.right_amount" min="1"/>
        </div>
        <div>
          <label>口径（锁死）</label>
          <select v-model="splitForm.mode" disabled>
            <option>整笔切分</option>
          </select>
        </div>
      </div>
      <div class="actions">
        <button @click="doSplit">整笔切分拆笼</button>
      </div>
      <table>
        <thead><tr><th>ID</th><th>笼</th><th>左</th><th>右</th><th>模式</th><th>状态</th></tr></thead>
        <tbody>
          <tr v-for="j in splitJobs" :key="j.id">
            <td>{{ j.id }}</td>
            <td>{{ j.cage_code }}</td>
            <td>{{ fmt(j.left_amount) }}</td>
            <td>{{ fmt(j.right_amount) }}</td>
            <td>{{ j.mode }}</td>
            <td><span class="badge done">{{ j.status }}</span></td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</section>
`;
