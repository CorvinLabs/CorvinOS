/**
 * Minimal typing for `react-cytoscapejs` (the package ships no types). Only the
 * props the console uses; `cy` hands back the real cytoscape Core instance.
 */
declare module "react-cytoscapejs" {
  import type { ComponentType, CSSProperties } from "react";
  import type cytoscape from "cytoscape";

  export interface CytoscapeComponentProps {
    elements: ReadonlyArray<object>;
    style?: CSSProperties;
    layout?: object;
    stylesheet?: ReadonlyArray<object>;
    cy?: (cy: cytoscape.Core) => void;
    className?: string;
  }

  const CytoscapeComponent: ComponentType<CytoscapeComponentProps>;
  export default CytoscapeComponent;
}
