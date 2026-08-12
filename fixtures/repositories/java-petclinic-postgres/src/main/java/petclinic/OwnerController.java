package petclinic;

import java.util.List;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/owners")
public class OwnerController {
    private final OwnerRepository owners;
    private final VisitRepository visits;

    public OwnerController(OwnerRepository owners, VisitRepository visits) {
        this.owners = owners;
        this.visits = visits;
    }

    @GetMapping("/{id}")
    public Owner showOwner(Integer id) {
        return owners.findById(id).orElseThrow();
    }

    @GetMapping("/search")
    public List<Owner> findByLastName(String lastName) {
        return owners.findByLastName(lastName);
    }

    @PostMapping("")
    public Owner createOwner(Owner owner) {
        return owners.save(owner);
    }

    @PostMapping("/visits")
    public Visit createVisit(Visit visit) {
        return visits.save(visit);
    }
}
