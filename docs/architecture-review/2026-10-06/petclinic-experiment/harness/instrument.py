"""Add a narrowly scoped OTel API interceptor; no telemetry SDK or value capture."""
from pathlib import Path
import hashlib, json
root=Path(__file__).resolve().parents[1]
pom=root/'petclinic/pom.xml'
text=pom.read_text()
dep='''    <!-- Experiment overlay: use the Java agent's SDK; API only. -->
    <dependency>
      <groupId>io.opentelemetry</groupId>
      <artifactId>opentelemetry-api</artifactId>
      <version>1.59.0</version>
    </dependency>
'''
if 'Experiment overlay' not in text:
 text=text.replace('  <dependencies>\n','  <dependencies>\n'+dep,1)
 pom.write_text(text)
contract={
 'contractId':'petclinic-owner-details/v1',
 'input':{'namespace':'jdbc:h2:mem:petclinic','name':'PUBLIC.OWNERS'},
 'output':{'namespace':'logical://spring-petclinic','name':'owners/ownerDetails#owner-summary'},
 'valueEdges':[
  {'input':'first_name','output':'name','type':'DIRECT','subtype':'TRANSFORMATION','description':"HTML_TEXT(first_name + ' ' + last_name)"},
  {'input':'last_name','output':'name','type':'DIRECT','subtype':'TRANSFORMATION','description':"HTML_TEXT(first_name + ' ' + last_name)"},
  {'input':'address','output':'address','type':'DIRECT','subtype':'TRANSFORMATION','description':'HTML_TEXT(address)'},
  {'input':'city','output':'city','type':'DIRECT','subtype':'TRANSFORMATION','description':'HTML_TEXT(city)'},
  {'input':'telephone','output':'telephone','type':'DIRECT','subtype':'TRANSFORMATION','description':'HTML_TEXT(telephone)'}],
 'datasetInfluences':[{'input':'id','type':'INDIRECT','subtype':'FILTER','description':'owners.id = request.ownerId'}],
 'scope':'Owner summary table only; pets, visits, edit links, other routes excluded',
 'evidenceClass':'DECLARED_AND_EXECUTED'
}
raw=json.dumps(contract,sort_keys=True,separators=(',',':'))
(root/'contracts/owner-details-v1.json').write_text(raw+'\n')
digest=hashlib.sha256(raw.encode()).hexdigest()
java='''package org.springframework.samples.petclinic.system;

import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.api.trace.Span;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.method.HandlerMethod;
import org.springframework.web.servlet.HandlerInterceptor;
import org.springframework.web.servlet.ModelAndView;
import org.springframework.web.servlet.config.annotation.InterceptorRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

/** Experiment-only metadata declaration. Never reads request/model values. */
@Configuration
public class LineageExperimentConfiguration implements WebMvcConfigurer {
    @Value("${lineage.experiment.enabled:false}")
    private boolean enabled;

    @Override
    public void addInterceptors(InterceptorRegistry registry) {
        if (enabled) registry.addInterceptor(new OwnerSummaryLineage());
    }

    private static class OwnerSummaryLineage implements HandlerInterceptor {
        private static final String MATCH = "lineage.experiment.ownerSummary";

        @Override
        public void postHandle(HttpServletRequest request, HttpServletResponse response,
                Object handler, ModelAndView modelAndView) {
            try {
                if (handler instanceof HandlerMethod method
                        && method.getMethod().getName().equals("showOwner")
                        && method.getBeanType().getName().endsWith(".owner.OwnerController")
                        && modelAndView != null
                        && "owners/ownerDetails".equals(modelAndView.getViewName())) {
                    request.setAttribute(MATCH, Boolean.TRUE);
                }
            } catch (RuntimeException ignored) {
                // Instrumentation must never change application behavior.
            }
        }

        @Override
        public void afterCompletion(HttpServletRequest request, HttpServletResponse response,
                Object handler, Exception error) {
            try {
                if (Boolean.TRUE.equals(request.getAttribute(MATCH)) && error == null
                        && response.getStatus() >= 200 && response.getStatus() < 300) {
                    Span.current().addEvent("experiment.lineage.mapping", Attributes.builder()
                        .put("lineage.contract.id", "petclinic-owner-details/v1")
                        .put("lineage.contract.sha256", "DIGEST")
                        .put("lineage.evidence.class", "DECLARED_AND_EXECUTED")
                        .put("lineage.operation", "GET /owners/{ownerId}")
                        .put("lineage.mapping.json", "JSON")
                        .put("lineage.mapping.success", true)
                        .build());
                }
            } catch (RuntimeException ignored) {
                // Failure is reported as missing evidence by the external verifier.
            }
        }
    }
}
'''.replace('DIGEST',digest).replace('JSON',raw.replace('\\','\\\\').replace('"','\\"'))
(root/'petclinic/src/main/java/org/springframework/samples/petclinic/system/LineageExperimentConfiguration.java').write_text(java)
print('Contract digest:',digest)
