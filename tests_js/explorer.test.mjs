import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

for (const filename of ['../mart/explorer_template.html', '../docs/explorer/index.html']) {
  const source = await readFile(new URL(filename, import.meta.url), 'utf8');
  const implementation = source.match(/function axisRange\(values\) \{[\s\S]*?\n\}/)[0];
  const axisRange = vm.runInNewContext(implementation + '; axisRange');
  test(`${filename}: negative net revenue stays within the chart bounds`, () => {
    for (const values of [[-30, 80], [-60, -10], [0, 0], [20, 100]]) {
      const {min, max} = axisRange(values);
      assert.ok(max > min);
      assert.ok(values.every(value => value >= min && value <= max));
      assert.ok(min <= 0 && max >= 0);
    }
  });
  test(`${filename}: unavailable ratios remain distinct from zero in labels and CSV`, () => {
    const helpers = source.match(/const pct = [\s\S]*?const csvRate = .*?;/)[0];
    const {rate, rateRaw, pct, csvRate} = vm.runInNewContext(helpers + '; ({rate, rateRaw, pct, csvRate})');
    for (const row of [{g: 0, r: -10, q: 0}, {g: 100, r: -100, q: -100}]) {
      assert.equal(rate(row), null);
      assert.equal(pct(rate(row)), 'n/a');
      assert.equal(csvRate(rate(row)), '');
    }
    assert.equal(rateRaw({g: 0, r: -10}), null);
    assert.equal(csvRate(rate({g: 100, r: 0, q: 0})), '0.0000');
    assert.equal(pct(rate({g: 100, r: -10, q: 0})), '10.0%');
  });
  test(`${filename}: unavailable chart observations create gaps instead of zero points`, () => {
    const implementation = source.match(/function linePath\(values, x, y\) \{[\s\S]*?\n\}/)[0];
    const linePath = vm.runInNewContext(implementation + '; linePath');
    assert.equal(linePath([20, null, -10, 0], x => x * 10, y => 50 - y), 'M0 30 M20 60 L30 50');
    assert.equal(linePath([null, null], x => x, y => y), '');
  });
}
