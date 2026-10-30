/**
 * @typedef {{
 *   left_table: string,
 *   left_column: string,
 *   right_table: string,
 *   right_column: string,
 * }} JoinPreviewHop
 * @typedef {{
 *   entity_table: string,
 *   source_table: string,
 *   hops: JoinPreviewHop[],
 *   verified?: boolean,
 * }} JoinPreviewPath
 */

/**
 * Build display rows from the selected plan paths only. Catalog relationships
 * are candidates and must not be presented as joins used by this plan.
 *
 * @param {JoinPreviewPath[]} paths
 */
export function getJoinPreviewRows(paths) {
  return paths.map((path, index) => ({
    key: `${path.entity_table}-${path.source_table}-${index}`,
    title: `${path.entity_table} to ${path.source_table}`,
    path,
  }));
}