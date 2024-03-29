#!/usr/bin/env npx ts-node
import { startGroup, endGroup, error, info, setFailed } from "@actions/core";
import Ajv, { ValidateFunction } from "ajv";
import { promises as fs } from "fs";
import { basename } from "path";
import { load } from "js-yaml";

const workflowFolder = "../../.github/workflows";

interface WorkflowWithErrors {
  id: string;
  name: string;
  errors: string[];
}

async function checkWorkflow(
  workflowPath: string,
  validate: ValidateFunction
): Promise<WorkflowWithErrors> {
  let workflowErrors: WorkflowWithErrors = {
    id: workflowPath,
    name: null,
    errors: [],
  };

  try {
    const workflowFile = await fs.readFile(workflowPath, "utf8");
    const workflow = load(workflowFile);
    if (!validate(workflow)) {
      workflowErrors.errors = validate.errors.map((error) => error.message);
    }
  } catch (e) {
    workflowErrors.errors.push(e.message);
  }

  return workflowErrors;
}

async function checkWorkflows(
  folder: string,
  validate: ValidateFunction
): Promise<WorkflowWithErrors[]> {
  const result: WorkflowWithErrors[] = [];

  const dir = await fs.readdir(folder, { withFileTypes: true });
  for (const file of dir) {
    if (basename(file.name).startsWith("_")) {
      continue;
    }
    if (file.isFile()) {
      const workflowWithErrors = await checkWorkflow(
        `${folder}/${file.name}`,
        validate
      );
      if (workflowWithErrors.errors.length > 0) {
        result.push(workflowWithErrors);
      }
    }
  }

  return result;
}

(async function main() {
  try {
    const ajv = new Ajv({ strict: false });
    const draft6MetaSchema = require("ajv/dist/refs/json-schema-draft-06.json");
    ajv.addMetaSchema(draft6MetaSchema);
    const githubWorkflow = require("./github-workflow.json");
    const validate = ajv.compile(githubWorkflow);

    const erroredWorkflows = await checkWorkflows(workflowFolder, validate);
    if (erroredWorkflows.length > 0) {
      startGroup(
        `😟 - Found ${erroredWorkflows.length} workflows with errors:`
      );
      erroredWorkflows.forEach((erroredWorkflow) => {
        error(
          `Errors in ${erroredWorkflow.id} - ${erroredWorkflow.errors
            .map((e) => e.toString())
            .join(", ")}`
        );
      });
      endGroup();
    } else {
      info("🎉🤘 - Found no workflows with errors!");
    }
  } catch (e) {
    error(`Unhandled error while validating workflows: ${e}`);
    setFailed(`Unhandled error`);
  }
})();

/* import { promises as fs } from "fs";
import { safeLoad } from "js-yaml";
import { basename, extname, join, dirname } from "path";
import { Validator as validator } from "jsonschema";
import { endGroup, error, info, setFailed, startGroup } from '@actions/core';

interface WorkflowWithErrors {
  id: string;
  name: string;
  errors: string[];
}

interface WorkflowProperties {
  name: string;
  description: string;
  creator: string;
  iconName: string;
  categories: string[];
}

const propertiesSchema = {
  type: "object",
  properties: {
    name: { type: "string", required: true , "minLength": 1},
    description: { type: "string", required: true },
    creator: { type: "string", required: false },
    iconName: { type: "string", required: true },
    categories: {
      anyOf: [
        {
          type: "array",
          items: { type: "string" }
        },
        {
          type: "null",
        }
      ],
      required: true
    },
  }
}

async function checkWorkflows(folders: string[], allowed_categories: object[]): Promise<WorkflowWithErrors[]> {
  const result: WorkflowWithErrors[] = []
  const workflow_template_names = new Set()
  for (const folder of folders) {
    const dir = await fs.readdir(folder, {
      withFileTypes: true,
    });

    for (const e of dir) {
      if (e.isFile() && [".yml", ".yaml"].includes(extname(e.name))) {
        const fileType = basename(e.name, extname(e.name))

        const workflowFilePath = join(folder, e.name);

        const workflowWithErrors = await checkWorkflow(workflowFilePath);
        if(workflowWithErrors.name && workflow_template_names.size == workflow_template_names.add(workflowWithErrors.name).size) {
          workflowWithErrors.errors.push(`Workflow template name "${workflowWithErrors.name}" already exists`) 
        }
        if (workflowWithErrors.errors.length > 0) {
          result.push(workflowWithErrors)
        }
      }
    }
  }

  return result;
}

async function checkWorkflow(workflowPath: string): Promise<WorkflowWithErrors> {
  let workflowErrors: WorkflowWithErrors = {
    id: workflowPath,
    name: null,
    errors: []
  }
  try {
    const workflowFileContent = await fs.readFile(workflowPath, "utf8");
    safeLoad(workflowFileContent); // Validate yaml parses without error

    let v = new validator();
    const res = v.validate(properties, propertiesSchema)
    workflowErrors.errors = res.errors.map(e => e.toString())
    
    if (properties.iconName) {
      if(! /^octicon\s+/.test(properties.iconName)) {
        try {
          await fs.access(`../../icons/${properties.iconName}.svg`)
        } catch (e) {
          workflowErrors.errors.push(`No icon named ${properties.iconName} found`)
        }
      }
      else {
        let iconName = properties.iconName.match(/^octicon\s+(.*)/)
        if(!iconName || iconName[1].split(".")[0].length <= 0) {
          workflowErrors.errors.push(`No icon named ${properties.iconName} found`)
        }
      }
      
    }
    var path = dirname(workflowPath)
    var folder_categories = allowed_categories.find( category => category["path"] == path)["categories"]
    if (!workflowPath.endsWith("blank.yml")) {
      if(!properties.categories || properties.categories.length == 0) {
        workflowErrors.errors.push(`Workflow categories cannot be null or empty`)
      } 
      else if(!folder_categories.some(category => properties.categories[0].toLowerCase() == category.toLowerCase())) {
        workflowErrors.errors.push(`The first category in properties.json categories for workflow in ${basename(path)} folder must be one of "${folder_categories}. Either move the workflow to an appropriate directory or change the category."`)
      }
    }

    if(basename(path).toLowerCase() == 'deployments' && !properties.creator) {
      workflowErrors.errors.push(`The "creator" in properties.json must be present.`)
    }
  } catch (e) {
    workflowErrors.errors.push(e.toString())
  }
  return workflowErrors;
}

(async function main() {
  try {
    const settings = require("./settings.json");
    const erroredWorkflows = await checkWorkflows(
      settings.folders, settings.allowed_categories
    )

    if (erroredWorkflows.length > 0) {
      startGroup(`😟 - Found ${erroredWorkflows.length} workflows with errors:`);
      erroredWorkflows.forEach(erroredWorkflow => {
        error(`Errors in ${erroredWorkflow.id} - ${erroredWorkflow.errors.map(e => e.toString()).join(", ")}`)
      })
      endGroup();
      setFailed(`Found ${erroredWorkflows.length} workflows with errors`);
    } else {
      info("🎉🤘 - Found no workflows with errors!")
    }
  } catch (e) {
    error(`Unhandled error while syncing workflows: ${e}`);
    setFailed(`Unhandled error`)
  }
})();
    */
